# Open Filmmaker Toolkit export

Read this only when the user asks for an Open Filmmaker Toolkit project file, or approves it in the delivery question.

Create a standalone `.oft.json` project from the same analysis spec used for the document:

```bash
python3 "${SKILL_DIR}/scripts/export_oft.py" \
  --spec "$OUT_DIR/spec.json" \
  --out "$OUT_DIR/analysis.oft.json"
```

The exporter creates one video asset per analyzed video, puts a readable version of its timestamped analysis in the standard asset `notes` field, and stores structured sections and frame references in an `analysis` field on that asset. It uses the OFT project `format` and `formatVersion` fields, and frame images are referenced by paths relative to the project file rather than embedded. Keep the referenced frame files at those relative paths when moving the project. The OFT schema permits additional asset fields, and Toolkit tools preserve fields they do not edit; this export does not require a Toolkit schema change. Existing output files are never overwritten.

Give the user the path of the resulting `.oft.json` file. Do not attempt the export if the user declined it.
