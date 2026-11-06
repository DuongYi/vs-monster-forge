# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import spine_timing as st  # noqa: E402

SPINE = {
    "skeleton": {"spine": "4.2.18"},
    "bones": [{"name": "root"}],
    "animations": {
        "idle": {"bones": {"body": {"translate": [{"y": 0}, {"time": 0.5, "y": 3}, {"time": 1.0, "y": 0}]}}},
        "attack_1": {
            "slots": {"weapon": {"attachment": [{"time": 0.25, "name": "slash"}]}},
            "bones": {"arm": {"rotate": [{"value": 0}, {"time": 0.8333, "value": 40, "curve": [0.2, 0, 0.8, 1]}]}},
            "attachments": {"default": {"body": {"body": {"deform": [{"time": 0.6, "vertices": [0, 1]}]}}}},
            "drawOrder": [{"time": 0.3, "offsets": [{"slot": "weapon", "offset": 1}]}],
            "events": [{"time": 0.4, "name": "hit"}, {"time": 0.7, "name": "hit"}, {"time": 0.1, "name": "sfx"}],
        },
        "die": {"bones": {"body": {"rotate": [{"time": 1.2, "value": 90}]}}},
        "pose": {},
    },
}


def test_durations_and_events(tmp_path):
    p = tmp_path / "lang_dau.json"
    p.write_text(json.dumps(SPINE), encoding="utf-8")
    info = st.read_spine(p)
    assert info["spine_version"] == "4.2.18"
    assert abs(info["animations"]["attack_1"]["duration_s"] - 0.8333) < 1e-6
    assert info["animations"]["pose"]["duration_s"] == 0.0

    spec = st.seed_spec(info, 12.0, "mob_lang_dau_01", 180, None, str(p))
    atk = spec["animations"]["attack_1"]
    assert atk["frames"] == 10                      # 0.8333 s × 12 fps
    assert atk["target_duration_ms"] == 833.3
    assert atk["events"] == {"hit": [5, 8], "sfx": 1}
    assert atk["target_events_ms"] == {"hit": [400.0, 700.0], "sfx": 100.0}
    assert atk["loop"] is False
    assert spec["animations"]["idle"]["loop"] is True
    assert spec["animations"]["idle"]["frames"] == 12
    assert spec["animations"]["die"]["frames"] == 14  # 1.2 s × 12 = 14.4
    assert any("pose" in w for w in spec["_source"]["warnings"])


def test_only_filter_and_cli(tmp_path):
    p = tmp_path / "Lang Dau.json"
    p.write_text(json.dumps(SPINE), encoding="utf-8")
    out = tmp_path / "spec.json"
    assert st.main([str(p), "--fps", "10", "--only", "idle,attack_1,run", "--out", str(out)]) == 0
    spec = json.loads(out.read_text("utf-8"))
    assert spec["monster_id"] == "lang_dau"
    assert set(spec["animations"]) == {"idle", "attack_1"}
    assert any("run" in w for w in spec["_source"]["warnings"])


def test_not_spine(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    assert st.main([str(p)]) == 1
