# RimWorld-Defs-Explorer

**RimWorld-Defs-Explorer** is a *local*, searchable browser for every `<Defs>`
and patch-operation XML in RimWorld **and** its DLCs.  
It builds a single, self-contained **HTML** file you can open in any browser—
no mods, servers, or game launch required.

## What it does

| Feature | Details |
|---------|---------|
| **Full-text search** | defType, defName, labels, descriptions, field values, file paths, raw XML — all highlighted |
| **Filters** | by **Source** (Core / each DLC) and **DefType**, with “All / None” presets & instant typing filter |
| **Details tabs** | **Overview** • **Inheritance** • **Raw XML** • **Similar Tags** (unique or full list) |
| **Offline viewer** | Generates one `RimDefs.html`. Double-click to open—even without RimWorld running |
| **Copy helpers** | one-click: `defType:defName`, file path, raw XML |
| **Patch support** | PatchOperation files listed alongside Defs, clearly marked `PATCH` |

## Quick Start

1. **Download** `build_defs_browser.py`  
   &nbsp;&nbsp;→ https://raw.githubusercontent.com/traecneh/RimWorld-Defs-Explorer/main/build_defs_browser.py

2. **Move** the file into your RimWorld **Data** folder  
   *Steam default path*  
   `C:\Program Files (x86)\Steam\steamapps\common\RimWorld\Data`

3. **Right-click** the script and select Open With and select Python (or run `py build_defs_browser.py`).  
   It scans **Core + all DLCs** and creates `RimDefs.html` in the same folder.

4. **Open** `RimDefs.html` in your browser and explore!

## Legal / EULA notice

The generated HTML embeds RimWorld’s XML data.  
That content is © **Ludeon Studios** and covered by the
[RimWorld EULA](https://store.steampowered.com/eula/294100_eula_1).

> **Do not upload, publish, or redistribute** the generated `RimDefs.html`
> Each user must generate their own copy from their own RimWorld install.

This repository contains **only original code**, licensed under MIT.

## License

The **code** in this repository is released under the **MIT License**  
(see `LICENSE`).

RimWorld, its DLCs, and their XML data remain © Ludeon Studios.
