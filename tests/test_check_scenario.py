"""Tests for check_scenario.py. Run from the repo root: python -m unittest discover tests -v"""

import contextlib
import copy
import io
import tempfile
import tomllib
import unittest
from pathlib import Path

from check_scenario import check_file, main, problems

SHIPPED = sorted(Path("scenarios").glob("*.toml"))

VALID = {
    "committee": "Test Committee",
    "agenda": "Test agenda",
    "delegations": [
        {"name": n, "title": f"the delegation of {n}", "public_position": "p", "objectives": "o",
         "red_lines": "r", "style": "s"}
        for n in ("Alpha", "Beta_Land")
    ],
    "phases": [{"name": "Opening", "instruction": "Open the {committee} on {agenda}. Drafts: {drafts}"}],
}


def scenario(**changes) -> dict:
    sc = copy.deepcopy(VALID)
    sc.update(changes)
    return sc


class ShippedScenarios(unittest.TestCase):
    def test_every_shipped_scenario_is_valid(self):
        self.assertTrue(SHIPPED, "no scenarios found; run from the repo root")
        for path in SHIPPED:
            with self.subTest(path=str(path)):
                self.assertEqual(check_file(path), [])


class Problems(unittest.TestCase):
    def assertProblem(self, sc: dict, fragment: str):
        found = problems(sc)
        self.assertTrue(any(fragment in p for p in found), f"{fragment!r} not in {found}")

    def test_valid_scenario_has_no_problems(self):
        self.assertEqual(problems(scenario()), [])

    def test_missing_top_level_text(self):
        sc = scenario()
        del sc["agenda"]
        self.assertProblem(sc, "missing or empty 'agenda'")
        self.assertProblem(scenario(committee="  "), "missing or empty 'committee'")

    def test_max_hops_must_be_positive_int(self):
        for bad in (0, -1, 2.5, "4", True):
            with self.subTest(max_hops=bad):
                self.assertProblem(scenario(max_hops=bad), "max_hops")
        self.assertEqual(problems(scenario(max_hops=1)), [])

    def test_needs_two_delegations(self):
        sc = scenario()
        sc["delegations"] = sc["delegations"][:1]
        self.assertProblem(sc, "at least two")
        self.assertProblem(scenario(delegations="USA"), "at least two")

    def test_delegation_missing_field_names_the_delegation(self):
        sc = scenario()
        del sc["delegations"][1]["red_lines"]
        self.assertProblem(sc, "delegation 2 (Beta_Land): missing or empty 'red_lines'")

    def test_delegation_must_be_a_table(self):
        sc = scenario()
        sc["delegations"].append("Gamma")
        self.assertProblem(sc, "delegation 3: must be a [[delegations]] table")

    def test_duplicate_and_invalid_names(self):
        sc = scenario()
        sc["delegations"][1]["name"] = "Alpha"
        self.assertProblem(sc, "duplicate name 'Alpha'")
        sc["delegations"][1]["name"] = "Saudi Arabia"
        self.assertProblem(sc, "name may only use")

    def test_needs_a_phase(self):
        self.assertProblem(scenario(phases=[]), "at least one [[phases]]")

    def test_unknown_placeholder(self):
        sc = scenario(phases=[{"name": "Opening", "instruction": "Discuss {topic}, {agenda.x} and {drafts[0]}"}])
        found = problems(sc)
        self.assertEqual(len(found), 3, found)
        for field in ("{topic}", "{agenda.x}", "{drafts[0]}"):
            self.assertProblem(sc, f"unknown placeholder {field}")

    def test_format_spec_and_conversion_are_allowed(self):
        self.assertEqual(problems(scenario(phases=[{"name": "Opening", "instruction": "{agenda!r} {committee:>5}"}])), [])

    def test_positional_placeholder(self):
        self.assertProblem(scenario(phases=[{"name": "Opening", "instruction": "Speak about {}"}]),
                           "unknown placeholder {}")

    def test_stray_brace(self):
        for text in ('Reply as JSON: {"vote": "yes"}', "Unclosed { brace", "Lone } brace"):
            with self.subTest(text=text):
                found = problems(scenario(phases=[{"name": "Voting", "instruction": text}]))
                self.assertTrue(found, text)
                self.assertIn("literal brace", found[0])
        # Doubled braces are literal and must pass.
        self.assertEqual(problems(scenario(phases=[{"name": "Voting", "instruction": "Literal {{x}} ok"}])), [])

    def test_every_valid_instruction_formats_like_model_un(self):
        # The contract: an instruction that passes the check never raises in model_un.py's str.format call.
        for path in SHIPPED:
            for phase in tomllib.loads(path.read_text())["phases"]:
                phase["instruction"].format(agenda="a", committee="c", drafts="d")


class Cli(unittest.TestCase):
    def run_main(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(list(args))
        return code, out.getvalue()

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp, "bad.toml")
            bad.write_text('committee = "C"\nagenda = "A"\n')
            broken = Path(tmp, "broken.toml")
            broken.write_text("committee = \n")
            code, out = self.run_main(*map(str, SHIPPED))
            self.assertEqual(code, 0, out)
            code, out = self.run_main(str(SHIPPED[0]), str(bad), str(broken))
            self.assertEqual(code, 1)
            self.assertIn(f"ok   {SHIPPED[0]}", out)
            self.assertIn(f"FAIL {bad}", out)
            self.assertIn("cannot read", out)


if __name__ == "__main__":
    unittest.main()
