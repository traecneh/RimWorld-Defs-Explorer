# -*- coding: utf-8 -*-
r"""
RimWorld Data (Core + DLC) Defs & Patches Browser Builder
---------------------------------------------------------
Place this script in:
  C:\Program Files (x86)\Steam\steamapps\common\RimWorld\Data
and run it there.

It scans all subfolders (Core, Royalty, Ideology, Biotech, Anomaly, etc.), finds XMLs
with <Defs> or Patch operations, and builds a single-file HTML browser 'RimDefs.html'
tagged by Source. Patch operations are included and grouped by operation class.

Highlights:
- Sources: filter by Core/DLC; source chips in results and details.
- Search across everything (source, defType/opClass, defName, labels, full text, file path, raw XML).
- Results grouped by defType (ThingDef, StatDef, PatchOperationReplace, ...).
- Details: Overview (Fields panel), Inheritance (skipped for patches), Raw XML w/ syntax highlighting.
- Copy buttons: defType:defName, file path, Raw XML.
"""

from pathlib import Path
import xml.etree.ElementTree as ET
from xml.dom import minidom
import json
from datetime import datetime
import sys
from collections import defaultdict

BASIC_FIELD_NAMES = {"defName", "label", "description", "parentName", "abstract"}

# -----------------------------
# XML helpers
# -----------------------------

def get_text(elem, name):
    child = elem.find(name)
    if child is not None and child.text is not None:
        return child.text.strip()
    return None

def bool_from(elem, child_name, attr_name="Abstract"):
    v = get_text(elem, child_name)
    if v is not None:
        return v.strip().lower() in ("true", "1", "yes")
    attr = elem.attrib.get(attr_name)
    if attr is not None:
        return str(attr).strip().lower() in ("true", "1", "yes")
    return False

def pretty_xml_of_node(elem):
    rough = ET.tostring(elem, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    lines = [ln for ln in pretty.splitlines() if ln.strip()]
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    return "\n".join(lines)

def node_text_content(elem):
    return "".join(elem.itertext()).strip()

def summarize_child_node(node):
    """
    Summarize a first-level child:
    - Leaf text -> trimmed.
    - <li> list -> items.
    - Map-like children (all leaf text) -> key:value pairs.
    - Otherwise -> child tag names.
    """
    MAX_ITEMS = 12
    kids = list(node)

    if not kids:
        val = (node.text or "").strip()
        if not val:
            return "(empty)"
        short = val[:120]
        return short + ("…" if len(val) > 120 else "")

    if all(k.tag.lower() == "li" for k in kids):
        items = []
        for k in kids[:MAX_ITEMS]:
            text = node_text_content(k)
            items.append(text if text else "(empty)")
        more = f", +{len(kids)-MAX_ITEMS} more" if len(kids) > MAX_ITEMS else ""
        return f"[{', '.join(items)}{more}]"

    def _is_leaf_with_text(e):
        return len(list(e)) == 0 and (e.text or "").strip() != ""
    if all(_is_leaf_with_text(k) for k in kids):
        items = []
        for k in kids[:MAX_ITEMS]:
            key = k.tag
            val = (k.text or "").strip()
            items.append(f"{key}: {val}")
        more = f", +{len(kids)-MAX_ITEMS} more" if len(kids) > MAX_ITEMS else ""
        return f"[{', '.join(items)}{more}]"

    names = [k.tag for k in kids[:6]]
    more = f", +{len(kids)-6} more" if len(kids) > 6 else ""
    return f"[{', '.join(names)}{more}]"

def extract_top_level_fields(defnode):
    pairs = []
    for child in defnode:
        tag = child.tag
        if tag in BASIC_FIELD_NAMES:
            continue
        try:
            summary = summarize_child_node(child)
        except Exception:
            summary = "(unreadable)"
        pairs.append({"k": tag, "v": summary})
    pairs.sort(key=lambda p: (p["k"].lower(), len(p["v"])))
    return pairs

# -----------------------------
# Extraction (Defs)
# -----------------------------

def extract_defs_from_file(xml_path: Path, root_data: Path, source_name: str):
    """
    Parse a single XML file with <Defs> root, extract each child as a def.
    """
    results = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        if root.tag != "Defs":
            return results
        for defnode in root:
            def_type = defnode.tag
            def_name = get_text(defnode, "defName")
            if not def_name:
                continue
            label = get_text(defnode, "label")
            description = get_text(defnode, "description")
            parent_name = get_text(defnode, "parentName")
            abstract = bool_from(defnode, "abstract")

            raw_xml = pretty_xml_of_node(defnode)
            text_blob = "".join(defnode.itertext()).strip()
            kv_pairs = extract_top_level_fields(defnode)

            rel_path = str(xml_path.relative_to(root_data / source_name))
            rec_id = f"{source_name}|{def_type}:{def_name}"

            results.append({
                "id": rec_id,
                "kind": "def",
                "defType": def_type,
                "defName": def_name,
                "label": label,
                "description": description,
                "parentName": parent_name,
                "abstract": abstract,
                "filePath": str(xml_path),
                "relPath": rel_path,
                "source": source_name,
                "rawXml": raw_xml,
                "textBlob": text_blob,
                "kvPairs": kv_pairs,
            })
    except Exception as e:
        results.append({
            "parseError": str(e),
            "filePath": str(xml_path),
            "source": source_name
        })
    return results

# -----------------------------
# Extraction (Patches)
# -----------------------------

def _first_texts(root, tag, maxn=3):
    vals = []
    for el in root.findall(f".//{tag}"):
        txt = (el.text or "").strip()
        if txt:
            vals.append(txt)
        if len(vals) >= maxn:
            break
    return vals

def _short(s, n=100):
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"

def extract_patches_from_file(xml_path: Path, root_data: Path, source_name: str):
    """
    Parse a Patch XML file (root <Patch> OR any xml containing <Operation ...> or <li Class="...">).
    Returns a record for each operation (including nested ones found under <operations> lists).
    """
    results = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Quick check: if not a <Patch>, only proceed if we see Operation nodes or li[@Class]
        is_patch_like = (root.tag == "Patch") or root.findall(".//Operation") or root.findall(".//li[@Class]")
        if not is_patch_like:
            return results

        # Collect candidate operation elements: <Operation ...> and <li Class="..."> anywhere
        ops = []
        ops.extend(root.findall(".//Operation"))
        ops.extend(root.findall(".//li[@Class]"))

        # Deduplicate by element identity
        seen_ids = set()
        uniq_ops = []
        for el in ops:
            key = id(el)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            uniq_ops.append(el)

        if not uniq_ops:
            return results

        rel_path = str(xml_path.relative_to(root_data / source_name))
        file_stem = xml_path.stem

        for idx, opnode in enumerate(uniq_ops, start=1):
            op_class = opnode.attrib.get("Class", "").strip() or "PatchOperation"
            def_type = op_class  # group by operation class
            def_name = f"{file_stem}#{idx:04d}"  # deterministic within file
            xpaths = _first_texts(opnode, "xpath", maxn=3)
            label = xpaths[0] if xpaths else op_class

            raw_xml = pretty_xml_of_node(opnode)
            text_blob = "".join(opnode.itertext()).strip()
            kv_pairs = extract_top_level_fields(opnode)

            rec_id = f"{source_name}|{def_type}:{def_name}"

            results.append({
                "id": rec_id,
                "kind": "patch",
                "defType": def_type,
                "defName": def_name,
                "label": label,
                "description": None,
                "parentName": None,
                "abstract": False,
                "filePath": str(xml_path),
                "relPath": rel_path,
                "source": source_name,
                "rawXml": raw_xml,
                "textBlob": text_blob,
                "kvPairs": kv_pairs,
            })

    except Exception as e:
        results.append({
            "parseError": str(e),
            "filePath": str(xml_path),
            "source": source_name
        })
    return results

# -----------------------------
# Parent chains (Defs only)
# -----------------------------

def build_parent_chains(defs):
    """
    Build parent chains for each DEF using parentName across sources.
    Preference order when resolving a parent:
      1) same source
      2) Core
      3) any other (alphabetical)
    For PATCH records (kind='patch'), parent chain remains empty.
    Adds:
      - parentsChainIds: list of parent record ids (source|type:name)
      - parentsChainLabels: display labels "type:name [Source]" or "(missing)"
    """
    # Index only real Defs
    index = defaultdict(dict)
    for d in defs:
        if d.get("kind") != "def":
            continue
        key = (d["defType"], d["defName"])
        index[d["source"]][key] = d

    def resolve_parent(cur_source, def_type, parent_name):
        key = (def_type, parent_name)
        # same source
        if key in index.get(cur_source, {}):
            return index[cur_source][key]
        # Core
        if key in index.get("Core", {}):
            return index["Core"][key]
        # any other
        for src in sorted(index.keys()):
            if src in (cur_source, "Core"):
                continue
            if key in index[src]:
                return index[src][key]
        return None

    for d in defs:
        if d.get("kind") != "def":
            d["parentsChainIds"] = []
            d["parentsChainLabels"] = []
            continue

        chain_ids, chain_labels = [], []
        seen = set()
        cur_type = d["defType"]
        cur_source = d["source"]
        parent_name = d.get("parentName")
        steps = 0
        while parent_name:
            cycle_key = (cur_source, cur_type, parent_name)
            if cycle_key in seen:
                chain_labels.append("(stopped: cycle)")
                break
            seen.add(cycle_key)
            parent = resolve_parent(cur_source, cur_type, parent_name)
            if not parent:
                chain_labels.append(f"{cur_type}:{parent_name} (missing)")
                break
            chain_ids.append(parent["id"])
            chain_labels.append(f"{parent['defType']}:{parent['defName']} [{parent['source']}]")
            # climb
            parent_name = parent.get("parentName")
            cur_type = parent["defType"]
            cur_source = parent["source"]
            steps += 1
            if steps > 100:
                chain_labels.append("(stopped: depth>100)")
                break

        d["parentsChainIds"] = chain_ids
        d["parentsChainLabels"] = chain_labels

# -----------------------------
# HTML template (single-file)
# -----------------------------

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>RimDefs — Core + DLC + Patches Browser</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  :root {
    --bg: #0f172a;
    --panel: #111827;
    --text: #e5e7eb;
    --muted: #9ca3af;
    --accent: #38bdf8;
    --border: #1f2937;
    --chip: #374151;
    --mark: rgba(245, 158, 11, 0.35);

    --xml-name: #93c5fd;
    --xml-attr: #86efac;
    --xml-string: #fcd34d;
    --xml-comment: #9ca3af;
    pre mark { pointer-events:none; }

  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color: var(--text); background: var(--bg); }
  header { position: sticky; top: 0; z-index: 5; background: linear-gradient(180deg, rgba(17,24,39,0.95), rgba(17,24,39,0.8) 70%, rgba(17,24,39,0)); backdrop-filter: blur(6px); padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .grow { flex: 1 1 auto; }
  .pill { background: var(--panel); border: 1px solid var(--border); border-radius: 9999px; padding: 10px 14px; color: var(--text); outline: none; width: 100%; }
  .meta { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .container { display: grid; grid-template-columns: 300px 500px minmax(600px, 1fr); gap: 0; min-height: calc(100vh - 68px); }
  .twoCols { grid-template-columns: 500px minmax(600px, 1fr) !important; }
  aside, main, .detail { border-right: 1px solid var(--border); background: var(--panel); }
  aside { padding: 14px; overflow: auto; }
  aside h3 { margin: 12px 0 8px; font-size: 14px; color: var(--muted); }
  .types, .sources { display: grid; gap: 6px; }
  .type-controls, .source-controls { display: flex; gap: 6px; margin-bottom: 8px; }
  .mini-btn { font-size: 12px; padding: 4px 8px; border: 1px solid var(--border); background: #0b1220; border-radius: 8px; cursor: pointer; color: var(--text) !important; }
  .filter-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 8px; background: #0b1220; border: 1px solid var(--border); cursor: pointer; user-select: none; }
  .filter-item input { margin: 0; }
  .count { margin-left: auto; color: var(--muted); font-size: 12px; }
  main { overflow: auto; }
  .results-header { padding: 10px 12px; font-size: 12px; color: var(--muted); border-bottom: 1px solid var(--border); }
  .list { display: grid; }
  #resultsToolbar { gap:8px; padding:8px 12px; justify-content:flex-end; border-bottom:1px solid var(--border); }

  /* Groups */
  .group { border-bottom: 1px solid var(--border); }
  .group-header { display: flex; align-items: center; gap: 8px; padding: 10px 12px; cursor: pointer; user-select: none; background: #0b1220; }
  .group-header:hover { background: #0e1424; }
  .caret { display: inline-block; transition: transform 0.15s ease; }
  .collapsed .caret { transform: rotate(-90deg); }
  .group-title { font-weight: 700; color: var(--accent); }
  .group-count { margin-left: auto; color: var(--muted); font-size: 12px; }
  .group-items { display: grid; }
  .group.collapsed .group-items { display: none; }

  /* Rows */
  .rowitem { display: grid; grid-template-columns: 1fr; gap: 6px; padding: 10px 12px; border-top: 1px solid var(--border); cursor: pointer; }
  .rowitem:hover { background: #0b1220; }
  .name { font-weight: 600; }
  .sub { color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .srcchip { display:inline-block; font-size: 11px; background: var(--chip); border: 1px solid var(--border); padding: 1px 6px; border-radius: 9999px; margin-right: 6px; }
  .patchchip { display:inline-block; font-size: 11px; background: #4b5563; border: 1px solid var(--border); padding: 1px 6px; border-radius: 9999px; margin-left: 6px; }

  .detail { overflow: auto; }
  .detail .pad { padding: 14px; }
  .tabs { display: flex; gap: 8px; margin: 12px 0; }
  .tab { padding: 8px 10px; border: 1px solid var(--border); background: #0b1220; border-radius: 8px; cursor: pointer; font-size: 14px; }
  .tab.active { outline: 2px solid var(--accent); }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .card { background: #0b1220; border: 1px solid var(--border); border-radius: 12px; padding: 12px; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .chip { background: var(--chip); color: #d1d5db; padding: 4px 8px; border-radius: 9999px; font-size: 12px; }
  .btn { border: 1px solid var(--border); background: #0b1220; color: var(--text); padding: 6px 10px; border-radius: 8px; cursor: pointer; }
  .btn:hover { outline: 2px solid var(--accent); }
  pre { white-space: pre; overflow: auto; background: #0b1220; border: 1px solid var(--border); padding: 12px; border-radius: 12px; }
  .footer-space { height: 24px; }
  .warn { color: #fca5a5; }
  .kv { display: grid; grid-template-columns: 220px 1fr; gap: 8px; }
  .kv .k { color: var(--muted); }
  mark { background: var(--mark); color: inherit; padding: 0 1px; border-radius: 2px; }
  /* XML syntax colors */
  .xml-tag .xml-name { color: var(--xml-name); }
  .xml-attr { color: var(--xml-attr); }
  .xml-string { color: var(--xml-string); }
  .xml-comment { color: var(--xml-comment); font-style: italic; }
  @media (max-width: 1200px) {
    .container { grid-template-columns: 260px 420px 1fr; }
    .twoCols { grid-template-columns: 420px 1fr !important; }
  }

  /* ── Tag-value overlay ─────────────────────────────── */
  #tagValuesWrap {
    position: fixed; inset: 0; z-index: 9000;
    background: rgba(0,0,0,.55); backdrop-filter: blur(2px);
    display: none; align-items: center; justify-content: center;
  }
  #tagValuesBox {
    max-height: 80vh; max-width: 600px; overflow: auto;
    background: #111827; border: 1px solid var(--border); border-radius: 12px;
    padding: 20px 24px; color: var(--text);
  }
  #tagValuesBox h2 { margin: 0 0 12px; font-size: 18px; }
  #tagValuesBox ul { margin: 0; padding: 0; list-style: none; max-height: 70vh; overflow: auto; }
  #tagValuesBox li { padding: 2px 0; font-family: ui-monospace, monospace; }
  #tagValuesBox .close { cursor: pointer; float: right; font-size: 18px; margin-left: 8px; }


</style>
</head>
<body>
<header>
  <div class="row">
    <input id="search" class="pill grow mono" placeholder="Search everything (source, types, names, labels, paths, raw XML, text)..." />
    <button id="clearBtn" class="btn">Clear</button>
    <button id="toggleFiltersBtn" class="btn">Hide Filters</button>
  </div>
  <div class="meta">
    Data root: <span class="mono">__DATA_ROOT__</span> • Generated: __GEN_TIME__ •
    <span id="metaCounts">__TOTAL__ records across __SRC_COUNT__ source(s)</span>
  </div>
</header>

<div class="container" id="container">
  <aside id="sidebar">
    <h3>Filter by Source</h3>
    <div class="row" style="gap:6px; margin:8px 0;">
      <input id="sourceSearch" class="pill mono" placeholder="Filter sources..." />
    </div>
    <div class="source-controls">
      <button id="sourcesAll" class="mini-btn">All</button>
      <button id="sourcesNone" class="mini-btn">None</button>
    </div>
    <div class="sources" id="sources"></div>

    <h3>Filter by Def Type</h3>
    <div class="row" style="gap:6px; margin:8px 0;">
      <input id="typeSearch" class="pill mono" placeholder="Filter types (ThingDef, PatchOperation..., etc.)" />
    </div>
    <div class="type-controls">
      <button id="typesAll" class="mini-btn">All</button>
      <button id="typesNone" class="mini-btn">None</button>
    </div>
    <div class="types" id="types"></div>
  </aside>

  <main>
    <div class="results-header" id="resultsHeader">Results</div>
    <div class="row" id="resultsToolbar">
      <button id="expandAll" class="mini-btn">Expand All</button>
      <button id="collapseAll" class="mini-btn">Collapse All</button>
    </div>
    <div class="list" id="results"></div>
    <div class="footer-space"></div>
  </main>

  <div class="detail" id="detail">
    <div class="pad">
      <div id="detailTitle" class="row" style="gap:8px;"></div>

      <div class="row" style="gap:8px; margin-top:8px;">
        <button class="btn" id="copyIdBtn">Copy defType:defName</button>
        <button class="btn" id="copyPathBtn">Copy file path</button>
        <button class="btn" id="copyRawBtn">Copy Raw XML</button>
      </div>

      <div class="tabs">
        <div class="tab active" data-tab="overview">Overview</div>
        <div class="tab" data-tab="inheritance">Inheritance</div>
        <div class="tab" data-tab="raw">Raw XML</div>
        <div class="tab" data-tab="similar">Similar Tags</div>
      </div>

      <div id="tab-overview" class="tabpane">
        <div class="grid-2">
          <div class="card">
            <div><b>source</b>: <span id="ovSource" class="mono"></span></div>
            <div><b>defType</b>: <span id="ovType" class="mono"></span></div>
            <div><b>defName</b>: <span id="ovName" class="mono"></span></div>
            <div><b>label</b>: <span id="ovLabel"></span></div>
            <div><b>abstract</b>: <span id="ovAbs"></span></div>
            <div><b>parentName</b>: <span id="ovParent" class="mono"></span></div>
            <div><b>file</b>: <span id="ovFile" class="mono"></span></div>
          </div>
          <div class="card">
            <div><b>description</b>:</div>
            <div id="ovDesc" style="margin-top:6px;"></div>
          </div>
        </div>

        <div class="card" style="margin-top:12px;">
          <div style="margin-bottom:8px;"><b>Fields (top-level)</b> <span class="sub">— quick view of common values</span></div>
          <div id="ovFields" class="kv"></div>
        </div>
      </div>

      <div id="tab-inheritance" class="tabpane" style="display:none;">
        <div class="card">
          <div id="inhHeader"><b>Parent chain (nearest first)</b></div>
          <div id="inhChain" class="chips" style="margin-top:8px;"></div>
          <div class="warn" id="inhNote" style="margin-top:8px; display:none;">
            Parent resolution prefers: same Source → Core → others. For deep merge semantics, inspect Raw XML.
          </div>
        </div>
      </div>

      <div id="tab-raw" class="tabpane" style="display:none;">
        <pre id="rawXml" class="mono"></pre>
      </div>

      <!-- Similar Tags pane -->
      <div id="tab-similar" class="tabpane" style="display:none;">
        <div class="pad">
          <div class="row" id="similarHeader" style="gap:8px; align-items:center;">
            <h3 id="similarTitle" style="margin:0;"></h3>
            <span class="grow"></span>
            <button class="mini-btn" id="similarUnique">Unique</button>
            <button class="mini-btn" id="similarAll">All</button>
          </div>
          <div id="similarBody" style="margin-top:12px;"></div>
        </div>
      </div>



    </div>
  </div>
</div>

<!-- Embedded data -->
<script id="rimdefs-data" type="application/json">__DATA_JSON__</script>

<script>
(function() {
  const raw = document.getElementById('rimdefs-data').textContent;
  const DATA = JSON.parse(raw);
  const records = DATA.records.filter(d => !d.parseError);
  const errors = DATA.records.filter(d => d.parseError);

  const defs = records; // includes defs and patches

  // Counts (types & sources)
  const typeCounts = (() => {
    const m = new Map();
    for (const d of defs) m.set(d.defType, (m.get(d.defType) || 0) + 1);
    return Array.from(m.entries()).sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0]));
  })();
  const sourceCounts = (() => {
    const m = new Map();
    for (const d of defs) m.set(d.source, (m.get(d.source) || 0) + 1);
    return Array.from(m.entries()).sort((a,b) => a[0].localeCompare(b[0]));
  })();

  // UI elements
  const q = (sel) => document.querySelector(sel);
  const qa = (sel) => Array.from(document.querySelectorAll(sel));
  const resultsEl = q('#results');
  const headerEl = q('#resultsHeader');
  const searchEl = q('#search');
  const containerEl = q('#container');
  const sidebarEl = q('#sidebar');
  const expandAllBtn = q('#expandAll');
  const collapseAllBtn = q('#collapseAll');

  const sourcesEl = q('#sources');
  const sourceSearchEl = q('#sourceSearch');
  const sourcesAllBtn = q('#sourcesAll');
  const sourcesNoneBtn = q('#sourcesNone');

  const typesEl = q('#types');
  const typeSearchEl = q('#typeSearch');
  const typesAllBtn = q('#typesAll');
  const typesNoneBtn = q('#typesNone');

  const similarTitle   = document.getElementById('similarTitle');
  const similarBody    = document.getElementById('similarBody');
  const similarUniqueBtn = document.getElementById('similarUnique');
  const similarAllBtn    = document.getElementById('similarAll');

  let similarMode = 'unique';   // 'unique' | 'all'
  let currentTag = null;        // remember last tag clicked

  const detail = {
    title: q('#detailTitle'),
    ovSource: q('#ovSource'),
    ovType: q('#ovType'),
    ovName: q('#ovName'),
    ovLabel: q('#ovLabel'),
    ovAbs: q('#ovAbs'),
    ovParent: q('#ovParent'),
    ovFile: q('#ovFile'),
    ovDesc: q('#ovDesc'),
    ovFields: q('#ovFields'),
    inhHeader: q('#inhHeader'),
    inhChain: q('#inhChain'),
    inhNote: q('#inhNote'),
    rawXml: q('#rawXml'),
    copyId: q('#copyIdBtn'),
    copyPath: q('#copyPathBtn'),
    copyRaw: q('#copyRawBtn'),
    tabs: qa('.tab'),
    similarContent: document.getElementById('similarContent'),
    similarContent: document.getElementById('similarBody'),    
    panes: { overview: q('#tab-overview'), inheritance: q('#tab-inheritance'), raw: q('#tab-raw'), similar: q('#tab-similar') }    
  };

  

  function collectUniqueValues(tagName){
    const set = new Set();
    for (const rec of defs){
      try{
        const doc = new DOMParser().parseFromString(rec.rawXml,"text/xml");
        doc.querySelectorAll(tagName).forEach(el=>{
          const v=(el.textContent||"").trim();
          if(v) set.add(v);
        });
      }catch(_){}
    }
    return Array.from(set);
  }

  function collectAllOccurrences(tagName){
    const out = [];
    for (const rec of defs){
      try{
        const doc = new DOMParser().parseFromString(rec.rawXml,"text/xml");
        doc.querySelectorAll(tagName).forEach(el=>{
          const v=(el.textContent||"").trim();
          if(v) out.push({val:v, src:rec.source, type:rec.defType, name:rec.defName});
        });
      }catch(_){}
    }
    return out;
  }

  function showValues(tagName){
    currentTag = tagName;
    if (similarMode === 'unique'){
      const vals = collectUniqueValues(tagName);
      const num  = vals.every(x=>/^[-+]?\d+(\.\d+)?$/.test(x));
      vals.sort(num ? (a,b)=>parseFloat(a)-parseFloat(b)
                    : (a,b)=>a.localeCompare(b,undefined,{numeric:true}));
      similarTitle.textContent = `<${tagName}> — ${vals.length} unique value${vals.length!==1?'s':''}`;
      similarBody.innerHTML = vals.map(v=>`<div class="mono">${escapeHtml(v)}</div>`).join("") || '(none)';
    } else {
      const occ = collectAllOccurrences(tagName);
      occ.sort((a,b)=>a.val.localeCompare(b.val,undefined,{numeric:true}) ||
                      a.src.localeCompare(b.src) ||
                      a.type.localeCompare(b.type) ||
                      a.name.localeCompare(b.name));
      similarTitle.textContent = `<${tagName}> — ${occ.length} total occurrence${occ.length!==1?'s':''}`;
      similarBody.innerHTML = occ.map(o=>`
        <div class="mono">
          <span class="chip">${escapeHtml(o.src)}</span>
          <span class="chip">${escapeHtml(o.type)}</span>
          ${escapeHtml(o.name)} — ${escapeHtml(o.val)}
        </div>`).join("") || '(none)';
    }
    showTab('similar');
  }

  similarUniqueBtn.onclick = () => {
    similarMode = 'unique';
    if (currentTag) showValues(currentTag);
  };
  similarAllBtn.onclick = () => {
    similarMode = 'all';
    if (currentTag) showValues(currentTag);
  };




  // State
  const state = {
    query: "",
    selectedId: null,
    groupOpen: Object.create(null), // defType -> boolean
    sourceFilter: new Set(sourceCounts.map(([s]) => s)), // all on
    typeFilter: new Set(typeCounts.map(([t]) => t)),     // all on
    sourceSearch: "",
    typeSearch: ""
  };

  // Helpers: highlighting & XML coloring
  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function escapeHtml(s) { return (s || "").replace(/[&<>\"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function highlightText(text, terms) {
    let html = escapeHtml(text || "");
    for (const t of terms) {
      if (!t) continue;
      const re = new RegExp(`(${escapeRegExp(t)})`, 'ig');
      html = html.replace(re, '<mark>$1</mark>');
    }
    return html;
  }
  function highlightXML(xml) {
    if (!xml) return "";
    let s = xml.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    s = s.replace(/&lt;!--[\s\S]*?--&gt;/g, m => `<span class="xml-comment">${m}</span>`);
    s = s.replace(/&lt;(\/?)([a-zA-Z0-9:_-]+)((?:\s+[a-zA-Z0-9:_-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>=]+))?)*)\s*(\/?)&gt;/g,
      (full, openSlash, name, attrs, selfSlash) => {
        const attrsHtml = (attrs || "").replace(/\s+([a-zA-Z0-9:_-]+)(\s*=\s*)(?:"([^"]*)"|'([^']*)'|([^\s"'>=]+))/g,
          (m, aname, eq, dqs, sqs, uq) => {
            const val = dqs != null ? `"${dqs}"` : (sqs != null ? `'${sqs}'` : uq);
            return ` <span class="xml-attr">${aname}</span>${eq}<span class="xml-string">${val}</span>`;
          }
        );
        const slashOpen = openSlash ? '/' : '';
        const slashSelf = selfSlash ? ' /' : '';
        return `<span class="xml-tag">&lt;${slashOpen}<span class="xml-name">${name}</span>${attrsHtml}${slashSelf}&gt;</span>`;
      }
    );
    return s;
  }
  function markTermsInElement(root, terms) {
    const validTerms = terms.filter(Boolean);
    if (!validTerms.length) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    const nodes = [];
    let n;
    while (n = walker.nextNode()) if (n.nodeValue && n.nodeValue.trim()) nodes.push(n);
    for (const node of nodes) {
      let text = node.nodeValue, lower = text.toLowerCase(), pos = 0;
      const frag = document.createDocumentFragment();
      while (true) {
        let bestIdx = -1, bestTerm = "";
        for (const t of validTerms) {
          const i = lower.indexOf(t.toLowerCase(), pos);
          if (i !== -1 && (bestIdx === -1 || i < bestIdx)) { bestIdx = i; bestTerm = t; }
        }
        if (bestIdx === -1) break;
        if (bestIdx > pos) frag.appendChild(document.createTextNode(text.slice(pos, bestIdx)));
        const m = document.createElement('mark'); m.textContent = text.slice(bestIdx, bestIdx + bestTerm.length);
        frag.appendChild(m);
        pos = bestIdx + bestTerm.length;
      }
      if (pos > 0) {
        if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
        node.parentNode.replaceChild(frag, node);
      }
    }
  }

  // Filters (Source)
  function renderSources() {
    sourcesEl.innerHTML = "";
    const filter = state.sourceSearch.toLowerCase();
    for (const [s, count] of sourceCounts) {
      if (filter && !s.toLowerCase().includes(filter)) continue;
      const id = 'source-' + s;
      const wrap = document.createElement('label');
      wrap.className = 'filter-item';
      wrap.innerHTML = `
        <input type="checkbox" id="${id}" ${state.sourceFilter.has(s) ? "checked" : ""} />
        <span class="mono">${s}</span>
        <span class="count">${count}</span>
      `;
      const input = wrap.querySelector('input');
      input.addEventListener('change', () => {
        if (input.checked) state.sourceFilter.add(s);
        else state.sourceFilter.delete(s);
        renderResults();
      });
      sourcesEl.appendChild(wrap);
    }
  }
  sourcesAllBtn.addEventListener('click', () => { state.sourceFilter = new Set(sourceCounts.map(([s]) => s)); renderSources(); renderResults(); });
  sourcesNoneBtn.addEventListener('click', () => { state.sourceFilter = new Set(); renderSources(); renderResults(); });
  sourceSearchEl.addEventListener('input', () => { state.sourceSearch = sourceSearchEl.value; renderSources(); });

  // Filters (Type)
  function renderTypes() {
    typesEl.innerHTML = "";
    const filter = state.typeSearch.toLowerCase();
    for (const [t, count] of typeCounts) {
      if (filter && !t.toLowerCase().includes(filter)) continue;
      const id = 'type-' + t;
      const wrap = document.createElement('label');
      wrap.className = 'filter-item';
      wrap.innerHTML = `
        <input type="checkbox" id="${id}" ${state.typeFilter.has(t) ? "checked" : ""} />
        <span class="mono">${t}</span>
        <span class="count">${count}</span>
      `;
      const input = wrap.querySelector('input');
      input.addEventListener('change', () => {
        if (input.checked) state.typeFilter.add(t);
        else state.typeFilter.delete(t);
        renderResults();
      });
      typesEl.appendChild(wrap);
    }
  }
  typesAllBtn.addEventListener('click', () => { state.typeFilter = new Set(typeCounts.map(([t]) => t)); renderTypes(); renderResults(); });
  typesNoneBtn.addEventListener('click', () => { state.typeFilter = new Set(); renderTypes(); renderResults(); });
  typeSearchEl.addEventListener('input', () => { state.typeSearch = typeSearchEl.value; renderTypes(); });

  // Hide/Show sidebar
  q('#toggleFiltersBtn').addEventListener('click', () => {
    const two = containerEl.classList.toggle('twoCols');
    sidebarEl.style.display = two ? 'none' : '';
    q('#toggleFiltersBtn').textContent = two ? 'Show Filters' : 'Hide Filters';
  });

  // Search
  function tokenize(s) { return (s || "").trim().toLowerCase().split(/\s+/).filter(Boolean); }
  function matchRecord(rec, terms) {
    if (!state.sourceFilter.has(rec.source)) return false;
    if (!state.typeFilter.has(rec.defType)) return false;
    if (!terms.length) return true;
    const hay = (
      rec.source + " " + rec.defType + " " + rec.defName + " " +
      (rec.label || "") + " " + (rec.description || "") + " " +
      (rec.textBlob || "") + " " + (rec.filePath || "") + " " +
      (rec.rawXml || "")
    ).toLowerCase();
    return terms.every(t => hay.includes(t));
  }

  // Results (grouped by defType)
  function renderResults() {
    const terms = tokenize(state.query);
    const filtered = defs.filter(d => matchRecord(d, terms));

    const groups = new Map();
    for (const d of filtered) {
      if (!groups.has(d.defType)) groups.set(d.defType, []);
      groups.get(d.defType).push(d);
    }
    const sortedTypes = Array.from(groups.keys()).sort((a,b) => a.localeCompare(b));
    for (const t of sortedTypes) groups.get(t).sort((a,b) =>
      a.defName.localeCompare(b.defName) || a.source.localeCompare(b.source)
    );

    headerEl.textContent = `Results: ${filtered.length} record(s) • ${sortedTypes.length} type group(s)`;
    resultsEl.innerHTML = "";

    for (const type of sortedTypes) {
      const items = groups.get(type);
      if (!(type in state.groupOpen)) state.groupOpen[type] = true;
      const open = !!state.groupOpen[type];

      const group = document.createElement('div');
      group.className = 'group' + (open ? '' : ' collapsed');

      const header = document.createElement('div');
      header.className = 'group-header';
      header.innerHTML = `
        <span class="caret">▶</span>
        <span class="group-title mono">${escapeHtml(type)}</span>
        <span class="group-count">${items.length}</span>
      `;
      header.addEventListener('click', () => {
        state.groupOpen[type] = !state.groupOpen[type];
        group.classList.toggle('collapsed', !state.groupOpen[type]);
      });
      group.appendChild(header);

      const list = document.createElement('div');
      list.className = 'group-items';
      for (const d of items) {
        const hName = highlightText(d.defName, terms);
        const hLabel = d.label ? highlightText(d.label, terms) : "";
        const hPath = highlightText(d.relPath || d.filePath, terms);
        const src = highlightText(d.source, terms);
        const patchBadge = d.kind === 'patch' ? `<span class="patchchip">PATCH</span>` : "";

        const row = document.createElement('div');
        row.className = 'rowitem';
        row.dataset.id = d.id;
        row.innerHTML = `
          <div class="name"><span class="srcchip">[${src}]</span>${hName}${d.abstract ? ' <span class="sub">(abstract)</span>' : ''} ${patchBadge}</div>
          <div class="sub" title="${escapeHtml(d.filePath)}">${hLabel ? hLabel + " • " : ""}<span class="mono">${hPath}</span></div>
        `;
        row.addEventListener('click', (e) => { e.stopPropagation(); selectRecord(d.id, true); });
        list.appendChild(row);
      }
      group.appendChild(list);
      resultsEl.appendChild(group);
    }
  }

  // Tabs
  function clearActiveTabs() { detail.tabs.forEach(t => t.classList.remove('active')); Object.values(detail.panes).forEach(p => { if (p) p.style.display = 'none'; }); }
  function showTab(name) { clearActiveTabs(); const tab = detail.tabs.find(t => t.dataset.tab === name); tab.classList.add('active'); detail.panes[name].style.display = ''; }

  function renderFields(pairs, terms) {
    detail.ovFields.innerHTML = "";
    if (!pairs || !pairs.length) {
      const empty = document.createElement('div'); empty.className = 'sub'; empty.textContent = 'No additional top-level fields.'; detail.ovFields.appendChild(empty); return;
    }
    for (const {k, v} of pairs) {
      const kEl = document.createElement('div'); kEl.className = 'k mono'; kEl.innerHTML = highlightText(k, terms);
      const vEl = document.createElement('div'); vEl.className = 'v mono'; vEl.innerHTML = highlightText(v, terms);
      detail.ovFields.appendChild(kEl); detail.ovFields.appendChild(vEl);
    }
  }

  function selectRecord(id, pushHash) {
    const rec = defs.find(d => d.id === id);
    if (!rec) return;
    state.selectedId = id;
    if (pushHash) location.hash = encodeURIComponent(id);
    window.scrollTo({ top: 0, behavior: 'smooth' });

    const terms = tokenize(state.query);

    const chips = `
      <div class="chip mono">${escapeHtml(rec.source)}</div>
      <div class="chip mono">${escapeHtml(rec.defType)}</div>
      ${rec.kind === 'patch' ? '<div class="chip">PATCH</div>' : ''}
    `;
    detail.title.innerHTML = `
      ${chips}
      <div class="mono" style="font-weight:700;">${highlightText(rec.defName, terms)}</div>
      ${rec.abstract ? '<div class="chip">abstract</div>' : ''}
    `;

    detail.ovSource.textContent = rec.source;
    detail.ovType.textContent = rec.defType;
    detail.ovName.innerHTML = highlightText(rec.defName, terms);
    detail.ovLabel.innerHTML = highlightText(rec.label || "", terms);
    detail.ovAbs.textContent = rec.abstract ? "true" : "false";
    detail.ovParent.innerHTML = rec.kind === 'patch' ? '(n/a for patches)' : highlightText(rec.parentName || "", terms);
    detail.ovFile.innerHTML = highlightText(rec.filePath, terms);
    detail.ovDesc.innerHTML = highlightText(rec.description || "", terms);

    renderFields(rec.kvPairs, terms);

    // Inheritance: disable for patches
    detail.inhChain.innerHTML = "";
    if (rec.kind === 'patch') {
      detail.inhHeader.innerHTML = '<b>Parent chain</b>';
      const el = document.createElement('div'); el.className = 'sub'; el.textContent = 'Not applicable for patch operations.'; detail.inhChain.appendChild(el);
      detail.inhNote.style.display='none';
    } else {
      const ids = rec.parentsChainIds || [];
      const labels = rec.parentsChainLabels || [];
      if (!labels.length) {
        const el = document.createElement('div'); el.className = 'sub'; el.textContent = 'None'; detail.inhChain.appendChild(el); detail.inhNote.style.display='none';
      } else {
        for (let i=0;i<labels.length;i++) {
          const lbl = labels[i];
          const id2 = ids[i];
          const btn = document.createElement('button');
          btn.className = 'chip btn mono';
          btn.textContent = lbl;
          btn.title = 'Open parent';
          btn.disabled = !id2;
          if (id2) btn.addEventListener('click', () => selectRecord(id2, true));
          detail.inhChain.appendChild(btn);
        }
        detail.inhNote.style.display='';
      }
    }

    // Raw XML
    const highlighted = highlightXML(rec.rawXml || "");
    detail.rawXml.innerHTML = highlighted;
    detail.rawXml.addEventListener('click', e => {
      if (!e.shiftKey) return;
      e.preventDefault();
      window.getSelection().removeAllRanges();

      const t = e.target;
      if (t.classList.contains('xml-name')) {
        const tag = t.textContent.trim();
        if (tag) showValues(tag);
      }
    });

    markTermsInElement(detail.rawXml, terms);

    // Actions
    detail.copyId.onclick = async () => { await navigator.clipboard.writeText(`${rec.defType}:${rec.defName}`); };
    detail.copyPath.onclick = async () => { await navigator.clipboard.writeText(rec.filePath); };
    detail.copyRaw.onclick = async () => { await navigator.clipboard.writeText(rec.rawXml || ""); };

    showTab('overview');
  }

  function applyHash() {
    const h = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (!h) return;
    const rec = defs.find(d => d.id === h);
    if (rec) {
      if (!state.sourceFilter.has(rec.source)) { state.sourceFilter.add(rec.source); renderSources(); }
      if (!state.typeFilter.has(rec.defType)) { state.typeFilter.add(rec.defType); renderTypes(); }
      state.groupOpen[rec.defType] = true;
      renderResults();
      selectRecord(rec.id, false);
    }
  }

  // Tabs events
  detail.tabs.forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));

  // Search events
  let timer = null;
  function onSearchChanged() {
    state.query = searchEl.value;
    renderResults();
    if (state.selectedId) selectRecord(state.selectedId, false);
  }
  searchEl.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(onSearchChanged, 200); });
  q('#clearBtn').addEventListener('click', () => { searchEl.value = ''; state.query = ''; renderResults(); if (state.selectedId) selectRecord(state.selectedId, false); });

  // Results toolbar
  expandAllBtn.addEventListener('click', () => {
    const types = typeCounts.map(([t]) => t);
    for (const t of types) state.groupOpen[t] = true;
    document.querySelectorAll('.group').forEach(g => g.classList.remove('collapsed'));
  });
  collapseAllBtn.addEventListener('click', () => {
    const types = typeCounts.map(([t]) => t);
    for (const t of types) state.groupOpen[t] = false;
    document.querySelectorAll('.group').forEach(g => g.classList.add('collapsed'));
  });

  // Sidebar toggle
  q('#toggleFiltersBtn').addEventListener('click', () => {
    const two = containerEl.classList.toggle('twoCols');
    sidebarEl.style.display = two ? 'none' : '';
    q('#toggleFiltersBtn').textContent = two ? 'Show Filters' : 'Hide Filters';
  });

  // Init
  (function init(){
    // build filters first
    renderSources();
    renderTypes();
    // initial results
    renderResults();
    window.addEventListener('hashchange', applyHash);
    applyHash();
    // footer
    if (errors.length) {
      const meta = document.getElementById('metaCounts');
      meta.textContent += ` • ${errors.length} parse issues`;
    }
  })();
})();
</script>
</body>
</html>
"""

def make_html(data_json, meta):
    data_json_safe = data_json.replace("</", "<\\/")
    html = HTML_TEMPLATE
    html = html.replace("__DATA_JSON__", data_json_safe)
    html = html.replace("__DATA_ROOT__", meta["dataRoot"])
    html = html.replace("__GEN_TIME__", meta["generatedAt"])
    html = html.replace("__TOTAL__", str(meta["total"]))
    html = html.replace("__SRC_COUNT__", str(meta["sources"]))
    return html

# -----------------------------
# Main
# -----------------------------

def main():
    root_data = Path(__file__).parent.resolve()  # ...\RimWorld\Data
    sources = [p for p in root_data.iterdir() if p.is_dir()]
    records = []
    xml_files_scanned = 0

    for src_dir in sources:
        source_name = src_dir.name
        for xml_path in src_dir.rglob("*.xml"):
            xml_files_scanned += 1
            # Extract defs (if any)
            recs_defs = extract_defs_from_file(xml_path, root_data, source_name)
            if recs_defs:
                records.extend(recs_defs)
                continue  # typical Defs file: no need to try patches
            # Extract patches (if any)
            recs_patches = extract_patches_from_file(xml_path, root_data, source_name)
            if recs_patches:
                records.extend(recs_patches)

    defs = [d for d in records if "parseError" not in d]
    errors = [d for d in records if "parseError" in d]

    build_parent_chains(defs)

    payload = { "records": defs + errors }
    data_json = json.dumps(payload, ensure_ascii=False)

    meta = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataRoot": str(root_data),
        "total": len(defs),
        "sources": len(set(d["source"] for d in defs))
    }
    html = make_html(data_json, meta)

    out_path = root_data / "RimDefs.html"
    try:
        out_path.write_text(html, encoding="utf-8")
        print(f"[OK] Wrote {out_path}")
    except PermissionError:
        docs = Path.home() / "Documents"
        docs.mkdir(parents=True, exist_ok=True)
        out_path = docs / "RimDefs.html"
        out_path.write_text(html, encoding="utf-8")
        print("[WARN] Could not write inside Program Files. Wrote to:")
        print(f"       {out_path}")
    except Exception as e:
        print("[ERROR] Could not write output:", e)
        sys.exit(1)

    print(f"Scanned {xml_files_scanned} XML files • {meta['total']} records • {len(errors)} parse errors • {meta['sources']} source(s)")
    print("Open the HTML in your browser and search/filter away!")

if __name__ == "__main__":
    main()
