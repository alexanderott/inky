# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Local Development
```bash
# Start development server without hardware requirements
python src/inkypi.py --dev

# Install development dependencies
pip install -r install/requirements-dev.txt

# Install production dependencies
pip install -r install/requirements.txt
```

### Hardware Installation (Raspberry Pi)
```bash
# Install on Raspberry Pi with Inky displays
sudo bash install/install.sh

# Install on Raspberry Pi with Waveshare displays
sudo bash install/install.sh -W <model>  # e.g., -W epd7in3f

# Update existing installation
sudo bash install/update.sh

# Uninstall
sudo bash install/uninstall.sh
```

## Architecture Overview

InkyPi is a Flask-based web application for controlling E-Ink displays on Raspberry Pi. The architecture follows a plugin-based system with these core components:

### Core Structure
- **src/inkypi.py**: Main application entry point with Flask app setup
- **src/config.py**: Configuration management (device_dev.json for dev, device.json for production)
- **src/display/**: Display abstraction layer supporting Inky, Waveshare, and mock displays
- **src/plugins/**: Plugin system with base classes and individual plugins
- **src/blueprints/**: Flask blueprints for web routes (main, settings, plugin, playlist)

### Plugin System
All plugins inherit from `base_plugin/base_plugin.py` and follow this structure:
- **plugin-info.json**: Plugin metadata and configuration schema
- **settings.html**: Web UI for plugin configuration
- **<plugin_name>.py**: Main plugin logic with `generate()` method
- **render/**: Optional HTML/CSS templates for complex layouts

### Display Management
- **DisplayManager**: Abstracts different display types (Inky, Waveshare, Mock)
- **RefreshTask**: Background thread managing display updates and playlists
- Development mode uses MockDisplay that saves images to `mock_display_output/`

### Configuration
- Development: `src/config/device_dev.json` (port 8080)
- Production: `device.json` in project root (port 80, systemd service)
- Plugin settings stored in configuration file per plugin instance

## Development Notes

- Use `--dev` flag for local development on any platform (no Raspberry Pi needed)
- Mock display outputs are saved to `mock_display_output/latest.png`
- Plugin templates use Jinja2 with HTML-to-image rendering via screenshot
- Font management through `utils/app_utils.get_fonts()`
- All plugins must implement `generate()` method returning PIL Image
- Web interface runs on Flask with Waitress WSGI server