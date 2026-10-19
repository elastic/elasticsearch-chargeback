#!/usr/bin/env python3
"""Install (or update) the Chargeback companion agent, skills and tools.

Runs against the Kibana of the monitoring cluster where the Chargeback integration is
installed. Idempotent: anything that already exists is updated in place, so this doubles as
the upgrade path. Prefer scripts/install_chargeback_agent.sh from the repo root.

Usage:
    export KIBANA_URL=https://<deployment>.kb.<region>.<csp>.cloud.es.io
    export KIBANA_API_KEY=<base64 api key>
    python install.py            # install or update everything
    python install.py --verify   # only check what is present, change nothing
    python install.py --skip-workflows  # ES|QL tools/skills/agent only (no Kibana Workflows API)

Requires Python 3.8+. No third-party dependencies.
"""
import argparse
import copy
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets')

# Order matters. The workflow must exist before the workflow-typed tool that wraps it, and
# the tools must exist before the skills and agent that reference them by ID.
ORDER = ['workflows', 'tools', 'skills', 'agents']
ENDPOINT = {'tools': 'tools', 'skills': 'skills', 'agents': 'agents'}

def ssl_context(base):
    """Verify TLS by default; skip verification for localhost or KIBANA_INSECURE=1 (elastic-package)."""
    insecure = os.environ.get('KIBANA_INSECURE', '').lower() in ('1', 'true', 'yes')
    host = (base or '').lower()
    if insecure or '127.0.0.1' in host or 'localhost' in host:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def api(base, key, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base.rstrip('/') + path, data=data, method=method,
        headers={'Authorization': 'ApiKey ' + key,
                 'Content-Type': 'application/json',
                 'kbn-xsrf': 'true'})
    try:
        with urllib.request.urlopen(req, context=ssl_context(base), timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]
    except urllib.error.URLError as e:
        return 0, str(e)


def load(kind):
    """Load asset files for a kind, injecting skill content from its sibling .md."""
    d = os.path.join(ASSETS, kind)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.json'):
            continue
        obj = json.load(open(os.path.join(d, fn), encoding='utf-8'))
        cf = obj.pop('content_file', None)
        if cf:
            obj['content'] = open(os.path.join(d, cf), encoding='utf-8').read()
        out.append(obj)
    return out


def load_workflows():
    """Workflows are YAML and use a different API, so they are handled separately."""
    d = os.path.join(ASSETS, 'workflows')
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(('.yml', '.yaml')):
            text = open(os.path.join(d, fn), encoding='utf-8').read()
            m = re.search(r'^name:\s*(.+?)\s*$', text, re.M)
            if not m:
                sys.exit('%s has no top-level "name:" line' % fn)
            out.append((os.path.splitext(fn)[0], m.group(1).strip().strip('"\''), text))
    return out


def sync_workflows(base, key, verify):
    """Create or update each workflow and return {asset stem: real Kibana workflow id}.

    Kibana derives the workflow id from its name and does NOT guarantee it matches: if the
    slug is taken it appends a suffix (chargeback-cost-review-1). So the id is resolved by
    matching on name and handed to the workflow-typed tools rather than hardcoded.
    """
    st, listing = api(base, key, 'GET', '/api/workflows')
    existing = {}
    if st == 200 and isinstance(listing, dict):
        for w in listing.get('results', []):
            existing[w.get('name')] = w.get('id')

    resolved, problems = {}, 0
    for stem, name, yaml_text in load_workflows():
        wid = existing.get(name)
        if verify:
            print('  %-42s %s' % (stem, ('present as %s' % wid) if wid else 'MISSING'))
            problems += (wid is None)
            if wid:
                resolved[stem] = wid
            continue

        if wid:
            st2, res2 = api(base, key, 'PUT', '/api/workflows/workflow/%s' % wid,
                            {'yaml': yaml_text})
            if st2 in (200, 201):
                print('  %-42s updated (id %s)' % (stem, wid))
                resolved[stem] = wid
            else:
                print('  %-42s FAILED update HTTP %s: %s' % (stem, st2, res2))
                problems += 1
            continue

        st2, res2 = api(base, key, 'POST', '/api/workflows/workflow', {'yaml': yaml_text})
        if st2 in (200, 201) and isinstance(res2, dict) and res2.get('id'):
            resolved[stem] = res2['id']
            print('  %-42s created (id %s)' % (stem, res2['id']))
        else:
            print('  %-42s FAILED create HTTP %s: %s' % (stem, st2, res2))
            problems += 1
    return problems, resolved


def strip_workflow_tools_from_agent(obj, skipped_tool_ids):
    """Remove workflow-backed tool ids from an agent definition when workflows are skipped."""
    out = copy.deepcopy(obj)
    conf = out.get('configuration') or {}
    tools = conf.get('tools') or []
    new_tools = []
    for block in tools:
        ids = [t for t in block.get('tool_ids', []) if t not in skipped_tool_ids]
        if ids:
            new_tools.append({'tool_ids': ids})
    conf['tools'] = new_tools
    out['configuration'] = conf
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true',
                    help='report what exists without creating or updating anything')
    ap.add_argument('--skip-workflows', action='store_true',
                    help='skip Kibana Workflows and workflow-typed tools (local stacks without Workflows)')
    ap.add_argument('--skip-skills', action='store_true',
                    help='skip Agent Builder skills (stacks without the skills API)')
    args = ap.parse_args()

    base = os.environ.get('KIBANA_URL')
    key = os.environ.get('KIBANA_API_KEY')
    if not base or not key:
        sys.exit('Set KIBANA_URL and KIBANA_API_KEY first.')

    st, _ = api(base, key, 'GET', '/api/agent_builder/agents')
    if st != 200:
        sys.exit('Agent Builder is not reachable at %s (HTTP %s). Check the URL, the API key '
                 'privileges, and that Agent Builder is enabled (Kibana 9.2.0+).' % (base, st))

    skip_skills = args.skip_skills
    if not skip_skills:
        st_sk, _ = api(base, key, 'GET', '/api/agent_builder/skills')
        if st_sk == 404:
            print('Skills API not available (HTTP 404); continuing without skills.')
            skip_skills = True

    failures = 0
    workflow_ids = {}
    skipped_workflow_tools = set()
    if args.skip_workflows:
        skipped_workflow_tools = {
            t['id'] for t in load('tools') if t.get('type') == 'workflow'
        }
    for kind in ORDER:
        print('=== %s ===' % kind)
        if kind == 'workflows':
            if args.skip_workflows:
                print('  (skipped: --skip-workflows)')
                continue
            problems, workflow_ids = sync_workflows(base, key, args.verify)
            failures += problems
            continue
        if kind == 'skills' and skip_skills:
            print('  (skipped: skills API unavailable or --skip-skills)')
            continue
        for obj in load(kind):
            # A workflow-typed tool must point at the id Kibana actually assigned.
            if kind == 'tools' and obj.get('type') == 'workflow':
                if args.skip_workflows:
                    skipped_workflow_tools.add(obj['id'])
                    print('  %-42s SKIPPED (--skip-workflows)' % obj['id'])
                    continue
                stem = obj['configuration'].get('workflow_id')
                real = workflow_ids.get(stem)
                if not real:
                    print('  %-42s SKIPPED (workflow %r unavailable)' % (obj['id'], stem))
                    failures += 1
                    continue
                obj['configuration']['workflow_id'] = real
            if kind == 'agents' and skipped_workflow_tools:
                obj = strip_workflow_tools_from_agent(obj, skipped_workflow_tools)
            oid = obj['id']
            ep = '/api/agent_builder/%s' % ENDPOINT[kind]

            if args.verify:
                st, _ = api(base, key, 'GET', '%s/%s' % (ep, oid))
                if st != 200 and kind == 'tools' and oid in skipped_workflow_tools:
                    print('  %-42s skipped (workflow tool)' % oid)
                    continue
                print('  %-42s %s' % (oid, 'present' if st == 200 else 'MISSING'))
                failures += (st != 200)
                continue

            st, res = api(base, key, 'POST', ep, obj)
            if st in (200, 201):
                print('  %-42s created' % oid)
                continue
            # Already there: update. "type" is immutable and rejected on update.
            upd = {k: v for k, v in obj.items() if k not in ('id', 'type')}
            st2, res2 = api(base, key, 'PUT', '%s/%s' % (ep, oid), upd)
            if st2 in (200, 201):
                print('  %-42s updated' % oid)
            else:
                # Report both attempts. A 404 on update just means it did not exist, so the
                # create error is the informative one.
                print('  %-42s FAILED' % oid)
                print('      create HTTP %s: %s' % (st, res))
                if st2 != 404:
                    print('      update HTTP %s: %s' % (st2, res2))
                failures += 1

    print()
    if failures:
        print('%d problem(s). Nothing else was changed.' % failures)
        sys.exit(1)
    if args.skip_workflows:
        print('Done (workflows skipped). Open Agent Builder in Kibana and chat with "Chargeback companion".')
    else:
        print('Done. Open Agent Builder in Kibana and chat with "Chargeback companion".')


if __name__ == '__main__':
    main()
