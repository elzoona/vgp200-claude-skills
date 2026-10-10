# Regenerating upload_reference.json

`tests/fixtures/upload_reference.json` holds byte vectors produced by
gp200-studio's TypeScript `SysExCodec`. It is the ground truth the Python port
in `scripts/prst_upload.py` is tested against.

Regenerate it only when gp200-studio's wire format changes:

1. Write `src/core/__parity_export.test.ts` in a gp200-studio checkout (see
   the version in this repo's git history, task 4 of the implementation plan).
2. `npx vitest run src/core/__parity_export.test.ts`
3. Copy `/tmp/upload_reference.json` here.
4. Delete the throwaway test from the clone and confirm `git status` is clean.
5. Run `python3 tests/test_upload_parity.py -v` — a diff means the
   format changed and the Python port needs updating.
