"""Very simple OCR pipeline.

Reads book-cover images from ``eval_data/`` and returns a ``Book`` for each,
using Mistral's latest OCR model with a structured-output (document annotation)
format so the title and author come back already parsed.

Run directly to print results for every image in ``eval_data/``::

    MISTRAL_API_KEY=... uv run python ocr_pipeline.py
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from mistralai.client import Mistral
from mistralai.extra import response_format_from_pydantic_model
from pydantic import BaseModel

OCR_MODEL = "mistral-ocr-latest"
EVAL_DATA = Path(__file__).parent / "eval_data"


class Book(BaseModel):
    title: str
    author: str


def _image_data_url(path: Path) -> str:
    """Encode an image file as a base64 ``data:`` URL for the OCR request."""
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/webp;base64,{encoded}"


def extract_book(image_path: str | Path, client: Mistral | None = None) -> Book:
    """OCR a single cover image and return the extracted ``Book``."""
    client = client or Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    path = Path(image_path)
    response = client.ocr.process(
        model=OCR_MODEL,
        document={"type": "image_url", "image_url": _image_data_url(path)},
        document_annotation_format=response_format_from_pydantic_model(Book),
    )
    return Book.model_validate_json(response.document_annotation)


def run(folder: Path = EVAL_DATA) -> dict[str, Book]:
    """Run the pipeline over every image in ``folder``."""
    client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    return {p.name: extract_book(p, client) for p in sorted(folder.iterdir()) if p.is_file()}


if __name__ == "__main__":
    for name, book in run().items():
        print(f"{name}: {book.title!r} by {book.author!r}")
