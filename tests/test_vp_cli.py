"""Comprehensive unit & integration test suite for VeriPerf scripts/vp.py."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


VP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "vp.py"
sys.path.insert(0, str(VP_PATH.parent))

import vp  # noqa: E402


def run_vp(args: list[str], cwd: Path, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(VP_PATH), *args],
        cwd=str(cwd),
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )


class TestVeriPerfCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="veriperf_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_and_log(self) -> None:
        (self.tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
        res = run_vp(["init", "--root", str(self.tmp)], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        info = json.loads(res.stdout.strip())
        self.assertTrue(info["S"].startswith(".bughunt/perf-"))
        s_dir = self.tmp / info["S"]
        self.assertTrue((s_dir / "benches").is_dir())
        self.assertTrue((s_dir / "logs" / "fs-before.txt").is_file())
        self.assertTrue((s_dir / "worklog.md").is_file())

        log_res = run_vp(
            ["log", "--s", info["S"], "--status", "Phase 2 - Baseline", "--entry", "OPT-1 | target=app.py:1"],
            self.tmp,
        )
        self.assertEqual(log_res.returncode, 0, log_res.stderr)
        wl = (s_dir / "worklog.md").read_text(encoding="utf-8")
        self.assertTrue(wl.startswith("Status: Phase 2 - Baseline"))
        self.assertIn("OPT-1 | target=app.py:1", wl)

    def test_hotspot_scan_multi_lang(self) -> None:
        (self.tmp / "slow.py").write_text(
            "import re, sqlite3\n"
            "def bad_py(items, cur):\n"
            "    out = ''\n"
            "    raw = open('a.txt').read()\n"
            "    for x in items:\n"
            "        cur.execute('SELECT * FROM t WHERE id=?', (x,))\n"
            "        for y in items:\n"
            "            if x == y:\n"
            "                re.compile(r'\\d+')\n"
            "                out += str(y)\n"
            "    return out + raw\n",
            encoding="utf-8",
        )
        (self.tmp / "slow.js").write_text(
            "const fs = require('fs');\n"
            "function badJs(arr) {\n"
            "  const text = fs.readFileSync('big.txt', 'utf8');\n"
            "  for (let i = 0; i < arr.length; i++) {\n"
            "    fetch('/api/' + arr[i]);\n"
            "    for (let j = 0; j < arr.length; j++) {\n"
            "      new RegExp('abc');\n"
            "    }\n"
            "  }\n"
            "  return text;\n"
            "}\n",
            encoding="utf-8",
        )
        (self.tmp / "slow.go").write_text(
            "package main\n"
            "func badGo(xs []int) {\n"
            "  for _, a := range xs {\n"
            "    for _, b := range xs {\n"
            "      _ = a + b\n"
            "    }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        # Test files should be ignored
        (self.tmp / "test_ignore.py").write_text(
            "for i in range(10):\n    for j in range(10):\n        pass\n",
            encoding="utf-8",
        )

        init_info = json.loads(run_vp(["init", "--root", str(self.tmp)], self.tmp).stdout.strip())
        res = run_vp(["hotspot-scan", "--root", str(self.tmp), "--s", init_info["S"]], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = res.stdout.strip().splitlines()
        self.assertLessEqual(len(lines), 35)
        self.assertIn("N_PLUS_ONE_IO", res.stdout)
        self.assertIn("NESTED_LOOP", res.stdout)
        self.assertIn("UNBOUNDED_MEMORY_READ", res.stdout)
        self.assertIn("LOOP_ALLOC_OR_RECOMPILE", res.stdout)
        self.assertNotIn("test_ignore.py", res.stdout)

        scan_json = json.loads((self.tmp / init_info["S"] / "logs" / "hotspot-scan.json").read_text(encoding="utf-8"))
        cats = {h["category"] for h in scan_json["hotspots"]}
        self.assertEqual(cats, {"N_PLUS_ONE_IO", "NESTED_LOOP", "UNBOUNDED_MEMORY_READ", "LOOP_ALLOC_OR_RECOMPILE"})
        files = {h["file"] for h in scan_json["hotspots"]}
        self.assertTrue({"slow.py", "slow.js", "slow.go"}.issubset(files))

    def test_bench_run_protocol_and_limits(self) -> None:
        info = json.loads(run_vp(["init", "--root", str(self.tmp)], self.tmp).stdout.strip())
        s_rel = info["S"]

        # Reject <3 warmups and <15 runs
        bad_w = run_vp(["bench-run", "--s", s_rel, "--id", "opt1", "--phase", "baseline", "--warmup", "2", "--", "python", "-c", "print(1)"], self.tmp)
        self.assertNotEqual(bad_w.returncode, 0)
        bad_r = run_vp(["bench-run", "--s", s_rel, "--id", "opt1", "--phase", "baseline", "--runs", "14", "--", "python", "-c", "print(1)"], self.tmp)
        self.assertNotEqual(bad_r.returncode, 0)

        # Mode A: VERIPERF_METRIC harness across small/medium/large
        harness = self.tmp / s_rel / "benches" / "bench_opt1.py"
        harness.write_text(
            "import json\n"
            "for tier, base in [('small', 1_000_000), ('medium', 5_000_000), ('large', 20_000_000)]:\n"
            "    samples = [base + i * 10000 for i in range(20)]\n"
            "    print('VERIPERF_METRIC:' + json.dumps({'tier': tier, 'warmup': 5, 'runs': 20, 'samples_ns': samples, 'peak_kb': 128.5, 'result_hash': f'h_{tier}'}))\n",
            encoding="utf-8",
        )
        res_a = run_vp(
            ["bench-run", "--s", s_rel, "--id", "opt1", "--phase", "baseline", "--", sys.executable, str(harness)],
            self.tmp,
        )
        self.assertEqual(res_a.returncode, 0, res_a.stdout + res_a.stderr)
        out_lines = res_a.stdout.strip().splitlines()
        self.assertLessEqual(len(out_lines), 10)
        self.assertIn("primary=large", res_a.stdout)
        data = json.loads((self.tmp / s_rel / "logs" / "opt1_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "harness")
        self.assertAlmostEqual(data["tiers"]["large"]["p50_ms"], 20.095, places=3)
        self.assertEqual(data["tiers"]["large"]["p95_ms"], 20.18)

        # Mode B: external subprocess timing fallback
        res_b = run_vp(
            ["bench-run", "--s", s_rel, "--id", "opt2", "--phase", "baseline", "--warmup", "3", "--runs", "15",
             "--", sys.executable, "-c", "print('deterministic-ok')"],
            self.tmp,
        )
        self.assertEqual(res_b.returncode, 0, res_b.stdout + res_b.stderr)
        self.assertLessEqual(len(res_b.stdout.strip().splitlines()), 10)
        data_b = json.loads((self.tmp / s_rel / "logs" / "opt2_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(data_b["mode"], "subprocess")
        self.assertEqual(data_b["tiers"]["default"]["runs"], 15)

    def test_verify_opt_dual_gate_all_4_outcomes(self) -> None:
        # Setup minimal Python package with a passing unit test
        (self.tmp / "pkg.py").write_text("def compute(n):\n    return sum(range(n))\n", encoding="utf-8")
        (self.tmp / "test_pkg.py").write_text(
            "import unittest\nfrom pkg import compute\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertEqual(compute(5), 10)\n"
            "if __name__ == '__main__': unittest.main()\n",
            encoding="utf-8",
        )
        info = json.loads(run_vp(["init", "--root", str(self.tmp)], self.tmp).stdout.strip())
        s_rel = info["S"]
        sb_res = run_vp(["sandbox", "--s", s_rel, "--mode", "opt"], self.tmp)
        self.assertEqual(sb_res.returncode, 0, sb_res.stderr)

        # Reject --min-speedup < 15.0
        floor_res = run_vp(["verify-opt", "--s", s_rel, "--id", "opt1", "--min-speedup", "10.0"], self.tmp)
        self.assertNotEqual(floor_res.returncode, 0)

        def write_metric(tid: str, phase: str, large_ns: int, rhash: str) -> None:
            payload = {
                "id": tid,
                "phase": phase,
                "mode": "harness",
                "status": "OK",
                "cmd": [sys.executable, "-c", "pass"],
                "tiers": {
                    "small": vp._compute_tier_stats("small", 5, [1.0] * 20, 10.0, f"s_{rhash}"),
                    "large": vp._compute_tier_stats("large", 5, [large_ns / 1e6] * 20, 100.0, f"l_{rhash}"),
                },
            }
            (self.tmp / s_rel / "logs" / f"{tid}_{phase}.json").write_text(json.dumps(payload), encoding="utf-8")

        test_cmd = f"{sys.executable} -m unittest test_pkg.py"

        # Outcome 1: ACCEPTED (75% faster, hash matches, tests pass)
        write_metric("opt_accept", "baseline", 100_000_000, "eq1")
        write_metric("opt_accept", "opt", 25_000_000, "eq1")
        r1 = run_vp(["verify-opt", "--s", s_rel, "--id", "opt_accept", "--test-cmd", test_cmd], self.tmp)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("verdict=ACCEPTED", r1.stdout)
        self.assertLessEqual(len(r1.stdout.strip().splitlines()), 12)

        # Outcome 2: REJECTED_INSUFFICIENT_GAIN (8.5% faster < 15% threshold)
        write_metric("opt_noise", "baseline", 100_000_000, "eq2")
        write_metric("opt_noise", "opt", 91_500_000, "eq2")
        r2 = run_vp(["verify-opt", "--s", s_rel, "--id", "opt_noise", "--test-cmd", test_cmd], self.tmp)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("REJECTED_INSUFFICIENT_GAIN", r2.stdout)

        # Outcome 3: REJECTED_OUTPUT_MISMATCH (80% faster, hash differs)
        write_metric("opt_mismatch", "baseline", 100_000_000, "eq3a")
        write_metric("opt_mismatch", "opt", 20_000_000, "eq3b")
        r3 = run_vp(["verify-opt", "--s", s_rel, "--id", "opt_mismatch", "--test-cmd", test_cmd], self.tmp)
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertIn("REJECTED_OUTPUT_MISMATCH", r3.stdout)

        # Outcome 4: REJECTED_TEST_FAILURE (80% faster, hash matches, test fails in sandbox-opt)
        sb_pkg = self.tmp / s_rel / "sandbox-opt" / "pkg.py"
        sb_pkg.write_text("def compute(n):\n    return 999\n", encoding="utf-8")
        write_metric("opt_failtest", "baseline", 100_000_000, "eq4")
        write_metric("opt_failtest", "opt", 20_000_000, "eq4")
        r4 = run_vp(["verify-opt", "--s", s_rel, "--id", "opt_failtest", "--test-cmd", test_cmd], self.tmp)
        self.assertEqual(r4.returncode, 0, r4.stderr)
        self.assertIn("REJECTED_TEST_FAILURE", r4.stdout)

        # Summary aggregates all 4 cleanly
        sum_res = run_vp(["summary", "--s", s_rel], self.tmp)
        self.assertEqual(sum_res.returncode, 0, sum_res.stderr)
        self.assertIn("ACCEPTED (>=15%):** 1 | **REJECTED:** 3", sum_res.stdout)

    def test_sandbox_lifecycle_and_cleanliness(self) -> None:
        (self.tmp / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        info = json.loads(run_vp(["init", "--root", str(self.tmp)], self.tmp).stdout.strip())
        s_rel = info["S"]

        self.assertEqual(run_vp(["sandbox", "--s", s_rel, "--mode", "opt"], self.tmp).returncode, 0)
        (self.tmp / s_rel / "sandbox-opt" / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")

        diff_res = run_vp(["sandbox", "--s", s_rel, "--mode", "diff"], self.tmp)
        self.assertEqual(diff_res.returncode, 0)
        self.assertIn("files_changed=1", diff_res.stdout)
        self.assertIn("-    return 1", diff_res.stdout)
        self.assertIn("+    return 2", diff_res.stdout)

        clean_res = run_vp(["sandbox", "--s", s_rel, "--mode", "clean"], self.tmp)
        self.assertEqual(clean_res.returncode, 0)
        self.assertIn("cleanliness: CLEAN", clean_res.stdout)
        self.assertFalse((self.tmp / s_rel / "sandbox-opt").exists())

    def test_memory_blowup_ceiling_and_override(self) -> None:
        info = json.loads(run_vp(["init", "--root", str(self.tmp)], self.tmp).stdout.strip())
        s_rel = info["S"]
        for phase, ms, kb in (("baseline", 100.0, 100.0), ("opt", 50.0, 220.0)):
            payload = {
                "id": "opt_mem",
                "phase": phase,
                "mode": "harness",
                "status": "OK",
                "cmd": [sys.executable, "-c", "pass"],
                "tiers": {"large": vp._compute_tier_stats("large", 5, [ms] * 20, kb, "h1")},
            }
            (self.tmp / s_rel / "logs" / f"opt_mem_{phase}.json").write_text(json.dumps(payload), encoding="utf-8")

        r_rej = run_vp(["verify-opt", "--s", s_rel, "--id", "opt_mem", "--interleave", "off"], self.tmp)
        self.assertEqual(r_rej.returncode, 0, r_rej.stderr)
        self.assertIn("REJECTED_MEMORY_BLOWUP", r_rej.stdout)

        r_ok = run_vp(
            ["verify-opt", "--s", s_rel, "--id", "opt_mem", "--allow-mem-tradeoff", "--interleave", "off"],
            self.tmp,
        )
        self.assertEqual(r_ok.returncode, 0, r_ok.stderr)
        self.assertIn("verdict=ACCEPTED", r_ok.stdout)

    def test_interleaved_ab_auto_trigger(self) -> None:
        (self.tmp / "target_mod.py").write_text("VAL = 100\n", encoding="utf-8")
        info = json.loads(run_vp(["init", "--root", str(self.tmp)], self.tmp).stdout.strip())
        s_rel = info["S"]
        self.assertEqual(run_vp(["sandbox", "--s", s_rel, "--mode", "opt"], self.tmp).returncode, 0)
        (self.tmp / s_rel / "sandbox-opt" / "target_mod.py").write_text("VAL = 84\n", encoding="utf-8")

        bench_script = self.tmp / s_rel / "benches" / "bench_il.py"
        bench_script.write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, os.environ.get('VERIPERF_TARGET_ROOT', '.'))\n"
            "from target_mod import VAL\n"
            "runs = int(os.environ.get('VERIPERF_RUNS', '20'))\n"
            "warmup = int(os.environ.get('VERIPERF_WARMUP', '5'))\n"
            "print('VERIPERF_METRIC:' + json.dumps({'tier': 'large', 'warmup': warmup, 'runs': runs, 'samples_ns': [VAL * 1_000_000] * runs, 'peak_kb': 50.0, 'result_hash': 'same'}))\n",
            encoding="utf-8",
        )
        self.assertEqual(
            run_vp(["bench-run", "--s", s_rel, "--id", "opt_il", "--phase", "baseline", "--", sys.executable, str(bench_script)], self.tmp).returncode,
            0,
        )
        ver = run_vp(["verify-opt", "--s", s_rel, "--id", "opt_il", "--interleave", "auto", "--interleave-rounds", "2"], self.tmp)
        self.assertEqual(ver.returncode, 0, ver.stdout + ver.stderr)
        self.assertIn("interleaved=YES", ver.stdout)
        verify_json = json.loads((self.tmp / s_rel / "logs" / "opt_il_verify.json").read_text(encoding="utf-8"))
        self.assertTrue(verify_json["interleaved_ab"]["triggered"])
        self.assertEqual(verify_json["verdict"], "ACCEPTED")

    def test_canonical_hash_float_and_unordered(self) -> None:
        h1 = vp.canonical_hash({"b": [0.1 + 0.2, -0.0], "a": 1})
        h2 = vp.canonical_hash({"a": 1, "b": [0.3, 0.0]})
        self.assertEqual(h1, h2)

        h3 = vp.canonical_hash([{"x": 2}, {"x": 1}], unordered_lists=True)
        h4 = vp.canonical_hash([{"x": 1}, {"x": 2}], unordered_lists=True)
        self.assertEqual(h3, h4)

        cli_res = run_vp(["canonical-hash", "--unordered-lists", "--json-str", "[3, 1, 2]"], self.tmp)
        self.assertEqual(cli_res.returncode, 0, cli_res.stderr)
        self.assertEqual(cli_res.stdout.strip(), vp.canonical_hash([1, 2, 3], unordered_lists=True))

    def test_sandbox_save_rollback_and_auto_rollback(self) -> None:
        (self.tmp / "a.py").write_text("X = 'base_a'\n", encoding="utf-8")
        (self.tmp / "b.py").write_text("Y = 'base_b'\n", encoding="utf-8")
        info = json.loads(run_vp(["init", "--root", str(self.tmp)], self.tmp).stdout.strip())
        s_rel = info["S"]
        self.assertEqual(run_vp(["sandbox", "--s", s_rel, "--mode", "opt"], self.tmp).returncode, 0)
        sb = self.tmp / s_rel / "sandbox-opt"

        # Save before opt1, apply opt1 (accepted)
        self.assertEqual(run_vp(["sandbox", "--s", s_rel, "--mode", "save", "--id", "opt1"], self.tmp).returncode, 0)
        (sb / "a.py").write_text("X = 'opt1_accepted'\n", encoding="utf-8")
        for phase, ms in (("baseline", 100.0), ("opt", 50.0)):
            p = {"id": "opt1", "phase": phase, "mode": "harness", "status": "OK", "cmd": [sys.executable, "-c", "pass"],
                 "tiers": {"large": vp._compute_tier_stats("large", 5, [ms] * 20, 50.0, "ha")}}
            (self.tmp / s_rel / "logs" / f"opt1_{phase}.json").write_text(json.dumps(p), encoding="utf-8")
        self.assertIn("verdict=ACCEPTED", run_vp(["verify-opt", "--s", s_rel, "--id", "opt1", "--auto-rollback"], self.tmp).stdout)

        # Save before opt2, edit b.py, reject opt2 -> auto-rollback restores b.py while keeping opt1's a.py!
        self.assertEqual(run_vp(["sandbox", "--s", s_rel, "--mode", "save", "--id", "opt2"], self.tmp).returncode, 0)
        (sb / "b.py").write_text("Y = 'opt2_rejected'\n", encoding="utf-8")
        for phase, ms in (("baseline", 100.0), ("opt", 95.0)):
            p = {"id": "opt2", "phase": phase, "mode": "harness", "status": "OK", "cmd": [sys.executable, "-c", "pass"],
                 "tiers": {"large": vp._compute_tier_stats("large", 5, [ms] * 20, 50.0, "hb")}}
            (self.tmp / s_rel / "logs" / f"opt2_{phase}.json").write_text(json.dumps(p), encoding="utf-8")
        v2 = run_vp(["verify-opt", "--s", s_rel, "--id", "opt2", "--auto-rollback"], self.tmp)
        self.assertIn("verdict=REJECTED", v2.stdout)
        self.assertIn("rollback=AUTO_RESTORED", v2.stdout)
        self.assertEqual((sb / "a.py").read_text(encoding="utf-8"), "X = 'opt1_accepted'\n")
        self.assertEqual((sb / "b.py").read_text(encoding="utf-8"), "Y = 'base_b'\n")

    def test_canonical_hash_file_stdin_and_sig_figs(self) -> None:
        # Stdin & --json-file avoid Windows CLI length limits
        payload = {"rows": [0.1 + 0.2, -0.0, {"k": [3, 1, 2]}]}
        json_file = self.tmp / "payload.json"
        json_file.write_text(json.dumps(payload), encoding="utf-8")

        r_file = run_vp(["canonical-hash", "--json-file", str(json_file)], self.tmp)
        self.assertEqual(r_file.returncode, 0, r_file.stderr)
        r_stdin = run_vp(["canonical-hash"], self.tmp, stdin_text=json.dumps({"rows": [0.3, 0.0, {"k": [3, 1, 2]}]}))
        self.assertEqual(r_stdin.returncode, 0, r_stdin.stderr)
        self.assertEqual(r_file.stdout.strip(), r_stdin.stdout.strip())

        # Tiny scientific floats must NOT collapse to 0.0; --sig-figs keeps scientific precision
        h_a = vp.canonical_hash({"val": 1.23456789e-8}, float_digits=6)
        h_b = vp.canonical_hash({"val": 1.23456791e-8}, float_digits=6)
        h_c = vp.canonical_hash({"val": 9.87654321e-8}, float_digits=6)
        h_zero = vp.canonical_hash({"val": 0.0}, float_digits=6)
        self.assertEqual(h_a, h_b)
        self.assertNotEqual(h_a, h_c)
        self.assertNotEqual(h_a, h_zero)

        r_sf1 = run_vp(["canonical-hash", "--sig-figs", "5", "--json-str", '{"x": 1.23451e-9}'], self.tmp)
        r_sf2 = run_vp(["canonical-hash", "--sig-figs", "5", "--json-str", '{"x": 1.23454e-9}'], self.tmp)
        r_sf3 = run_vp(["canonical-hash", "--sig-figs", "5", "--json-str", '{"x": 5.55555e-9}'], self.tmp)
        self.assertEqual(r_sf1.returncode, 0)
        self.assertEqual(r_sf1.stdout.strip(), r_sf2.stdout.strip())
        self.assertNotEqual(r_sf1.stdout.strip(), r_sf3.stdout.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)

