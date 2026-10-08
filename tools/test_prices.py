import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import prices


class UnavailablePricingTest(unittest.TestCase):
    def test_unavailable_fund_holds_only_its_member_without_fetching_it(self):
        day = (datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            data = pathlib.Path(tmp)
            folder = data / "members" / day
            folder.mkdir(parents=True)
            entries = []
            for member, ticker in [("fund-holder", "FUND-TEST"), ("cash-holder", "CASH")]:
                filename = member + ".json"
                entries.append({"id": member, "file": filename})
                (folder / filename).write_text(json.dumps({
                    "member": member, "date": day,
                    "holdings": [{"ticker": ticker, "weight": 100}],
                }))
            (data / "manifest.json").write_text(json.dumps({
                "snapshots": [{"date": day, "members": entries}],
            }))
            (data / "instruments.json").write_text(json.dumps({
                "instruments": {"FUND-TEST": {"pricing": "unavailable"}},
            }))
            with patch.object(prices, "DATA", data), patch.object(prices, "fetch") as fetch:
                fetch.return_value = {"ccy": "KRW", "px": {day: 1}}
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(prices.main(), 0)
                self.assertEqual([call.args[0] for call in fetch.call_args_list], [prices.FX])
            result = json.loads((data / "results.json").read_text())
            self.assertEqual(result["onHold"], {"fund-holder": ["FUND-TEST"]})
            self.assertNotIn("fund-holder", result["members"])
            self.assertEqual(result["members"]["cash-holder"]["index"], 100)


if __name__ == "__main__":
    unittest.main()
