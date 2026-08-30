import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from config_utils import DEFAULT_CONFIG, CONFIG_VERSION, validate_config

base=dict(DEFAULT_CONFIG)
validate_config(base)

bad=dict(base);bad["stats_enbled"]=False
try: validate_config(bad)
except ValueError: pass
else: raise AssertionError("unknown config key accepted")

for key in ("port",):
    bad=dict(base); bad[key]=True
    try: validate_config(bad)
    except ValueError: pass
    else: raise AssertionError(f"boolean accepted for numeric key: {key}")

for old_version in (12,13,14,15,16):
    old=dict(base); old["config_version"]=old_version
    assert validate_config(old)["config_version"]==CONFIG_VERSION

bad=dict(base); bad["config_version"]=999
try: validate_config(bad)
except ValueError: pass
else: raise AssertionError("unknown config version accepted")

print("Config strict-schema and migration tests passed.")
