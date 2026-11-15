"""The bundled H3 backend defaults are separate from general workflow rules."""
import json
from pathlib import Path
PRESET_PATH = Path(__file__).resolve().parent.parent / 'presets' / 'h3-local.json'
PRESET = json.loads(PRESET_PATH.read_text(encoding='utf-8'))
GENERATION = PRESET['generation']
DEPTH = PRESET['depth']
MODELS = PRESET['models']
