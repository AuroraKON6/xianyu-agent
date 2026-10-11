"""Explicit supplier-original declarations for isolated tests, never live evidence."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import io


def declared_quote_fields(observed=None):
    observed = observed or datetime.now(timezone.utc)
    return {
        "source_kind": "supplier_original", "source_reference": "isolated-fixture-reference",
        "source_observed_at": observed.isoformat(),
        "valid_until": (observed + timedelta(hours=24)).isoformat(),
    }


def with_declared_quote_provenance(snapshot):
    result = deepcopy(snapshot)
    observed = datetime.fromisoformat(result["observed_at"])
    fields = declared_quote_fields(observed)
    result["fetched_at"] = result.get("fetched_at") or datetime.now(timezone.utc).isoformat()
    result["expires_at"] = fields["valid_until"]
    result.setdefault("attributes", {})["quote_provenance"] = dict(fields, schema_version=1)
    return result


def declared_quote_csv(content, observed=None):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    fields = declared_quote_fields(observed)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(reader.fieldnames) + list(fields))
    writer.writeheader()
    for row in reader:
        writer.writerow(dict(row, **fields))
    return out.getvalue().encode("utf-8")
