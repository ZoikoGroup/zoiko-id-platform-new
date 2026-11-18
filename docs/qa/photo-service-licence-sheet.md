# Photo-checking service: licence facts for the person who signs the register

Prepared 18 Nov 2026 by engineering from the **installed image** (`zoiko-id-ocr`, built from `tools/ocr-sidecar`), not from memory. It states what the files in the image say. It does not read a licence for anyone, confirm commercial use, or sign the register: that is a person's act (WF01-011), recorded in `backend/config/self-run-verification/engine-register.yaml` by setting `commercial_use`, `model_licence_confirmed` and `licence_verified_on`. The gate cannot check the person read it honestly; it can only refuse to run without the record.

## What the image contains and what its own files say

| Component | Version in the image | Licence its installed files state | Note |
|---|---|---|---|
| Tesseract OCR | 5.5.0 | Apache-2.0 (Debian `copyright`, `tesseract-ocr` and `libtesseract5`) | The register's `tesseract` entry |
| Tesseract language data | `tesseract-ocr-eng`, `-fra`, `-spa`, `-deu`, `-ara` (Debian packages) | Apache-2.0 (Debian `copyright` of `-eng` and `-fra` read; the others were not individually read) | Trained language data is a "model": the register asks that its licence be read, not only the library's |
| Pillow (Python imaging) | 12.3.0 | MIT-CMU (package metadata) | Pinned in the Dockerfile |
| Python | 3.13.18 | The Python Software Foundation licence (not read from the image) | Base image `python:3.13-slim` |
| Leptonica (used by Tesseract) | 1.84.1 | **Not confirmed**: the installed `copyright` file for `libleptonica6` is empty in the image | A person must read upstream's licence |
| Image libraries linked by Leptonica (libjpeg-turbo, libpng, libtiff, zlib, libwebp, libopenjp2, libgif) | as `tesseract --version` prints | Not read | Each is a Debian package with its own `copyright` file under `/usr/share/doc` |

## What a person must do before signing

1. Read the licence text for each row above that this sheet did not read (Python, Leptonica, the image libraries, the other language packs), starting from `/usr/share/doc/<package>/copyright` in the image and the upstream project.
2. Confirm that the use (running the service for a commercial product, with Tesseract's output used only to read printed lines and never as proof of authenticity) is allowed, including any notice or attribution the licences require.
3. Record who read it and when: set `commercial_use: YES`, `model_licence_confirmed: true` and `licence_verified_on: <date>` on the `tesseract` entry, or `NO` with the reason if any licence does not allow it.
4. Pin the dependency list for the adopted image and put its location in `sbom_ref`. The register currently has none.

## What would change the answer

A different OCR engine, a different language pack, an updated base image or a newer Tesseract changes this sheet. Re-read after any of them. Nothing in the code uses the service's output as evidence that a document is genuine.
