# Development checks

Use the client requirements from `requirements.txt`, plus FFmpeg/FFprobe. From the skill root:

```text
python -B -m unittest discover -s tests -v
python -B scripts/dance.py routes
python -B scripts/model_inventory.py --route ref2v --json
```

On macOS use the chosen `python3` executable; on Windows `py -3` or a full executable path works. Test media are short generated test patterns and tones, not portraits, dance footage or music. Tests do not contact ComfyUI or submit GPU jobs. Protocol tests use a loopback mock server; media recovery tests intentionally interrupt local steps and check that retries reuse completed work. FFmpeg-dependent tests are skipped when FFmpeg is absent; a skipped media test is not a recovery verification.

For a Windows encoding regression, also run the suite with the client interpreter's UTF-8 mode disabled:

```text
python -X utf8=0 -B -m unittest discover -s tests -v
```

Check native/gateway configuration, Chinese paths, BOM/non-BOM UTF-8, missing client tools, route-specific dependencies, preservation of reviewed acceptance, input-change rejection and same-job recovery after a lost response. Model selector validation and mock protocol tests are separate from inference and visual quality checks. Existing relevant inference evidence can be reused without a new dance-generation benchmark.

Do not commit model files, task directories, credentials, Python caches or personal source media. The model manifest lists externally hosted files and never authorizes their download or redistribution.
