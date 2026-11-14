from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.parsers.base import DocumentError

_NOT_DECRYPTED = 0


def open_pdf(data: bytes) -> PdfReader:
    """Open and check a PDF. Blocking: call it in a thread.

    A password gives `encrypted`. Damage or no page gives `invalid_pdf`. A PDF that opens
    with an empty password is not `encrypted`.
    """
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and int(reader.decrypt("")) == _NOT_DECRYPTED:
            raise DocumentError("encrypted", "The PDF file needs a password.")
        count = len(reader.pages)
        for index in range(count):  # load each page object: a cut-off file fails here
            reader.pages[index]
    except DocumentError:
        raise
    except (PyPdfError, ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError) as exc:
        raise DocumentError("invalid_pdf", "The PDF file is damaged.") from exc
    if count == 0:
        raise DocumentError("invalid_pdf", "The PDF file has no page.")
    return reader
