# Krita Lottie Export Plugin

Export animated Krita vector layers (including Figma-imported SVG vectors) to
Lottie JSON (`v5.7.1`).

## Features

- Exports vector layers to Lottie shape layers.
- Preserves opacity animation over timeline frames.
- Handles complex SVG input better:
  - nested transforms
  - `<use>` references
  - grouped style and opacity inheritance
  - common SVG shape/path commands
- Removes full-canvas white background rectangles when they would hide content.
- Preserves visual stacking for complex intra-layer overlap.
- Remembers last export folder.
- Lets you choose output JSON filename (prevents accidental overwrite).

## Requirements

- Krita with Python scripting enabled.
- PyQt5 (bundled with Krita in most installs).

## Installation

1. Copy plugin files into your Krita Python plugin folder:
   - Windows:
     `C:\Users\<YourUser>\AppData\Roaming\krita\pykrita\lottie_export`
2. Ensure Krita has an entry for this plugin in
   `C:\Users\<YourUser>\AppData\Roaming\krita\pykrita\`.
3. Restart Krita.
4. In Krita, enable the plugin if needed:
   `Settings -> Configure Krita -> Python Plugin Manager`.

## Usage

1. Open your animated Krita document.
2. Run:
   `Tools -> Scripts -> Export as Lottie JSON`.
3. Choose:
   - Output folder
   - Output filename (for example: `scene_01.json`)
   - Optional FPS override
   - Whether to skip invisible layers
4. Click **Export**.

## Notes

- Best results come from vector layers.
- For Figma workflows, import SVG into Krita vector layers first.
- Lottie layer visibility timing is derived from sampled opacity values.

## Limitations

- Advanced SVG clipping/masking and some path semantics may still vary by
  target Lottie renderer.
- This exporter currently focuses on shape + opacity workflows.

## License

MIT License. See `LICENSE`.
