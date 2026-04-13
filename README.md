# Krita Lottie Export Plugin

Exports Krita animation projects to Lottie JSON format.

## Features

- Exports animations to Lottie JSON
- Preserves layers and timing
- Simple export interface inside Krita
- Configurable export options

## Requirements

- Krita with Python support
- PyQt5 (usually included with Krita)

## Installation

1. Open Krita  
2. Go to:  
   `Settings → Manage Resources → Open Resource Folder`  
3. Inside the folder, open or create a `pykrita` directory if it doesn’t exist  
4. Copy the plugin folder into `pykrita`  
5. Restart Krita  
6. Enable the plugin in:  
   `Settings → Configure Krita → Python Plugin Manager`

## Usage

`Tools → Scripts → Export as Lottie JSON`

Choose export options and run the export.

## License

MIT