"""
Document Parser - Phase 3 (Structure Scanner)
converts a documents raw bytes into clean markdown for scoring/reformation.
- PDF,DOCX -> Docling
TXT,MD -> direct decode (no parsing needed)
"""
from io import BytesIO
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import DocumentStream

converter = DocumentConverter()

SUPPORTED_MIME_TYPES ={
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "text/plain",
    "text/markdown",
}

class UnsupportedDocumentTypeError(Exception):
    pass

class DocumentParseError(Exception):
    pass

def parse_document_to_markdown(file_data: bytes, mime_type:str, filename:str) -> str:
    """
    Parse a document's raw bytes into markdown.
    Args:
        file_data: raw bytes (as stored in document_versions.file_data)
        mime_type: the document's mime_type (already validated at upload time, Phase 1 §13.4)
        filename: original filename — used by Docling to infer format from extension

    Returns:
        Markdown string.

    Raises:
        UnsupportedDocumentTypeError: mime_type not in SUPPORTED_MIME_TYPES
        DocumentParseError: parsing failed for a supported type
    """

    if mime_type not in SUPPORTED_MIME_TYPES:
        raise UnsupportedDocumentTypeError(f"Unsupported mime_type: {mime_type}")
    if mime_type in {"text/plain", "text/markdown"}:
        try:
            return file_data.decode("utf-8")  #bytes -> str
        except UnicodeDecodeError:
            return file_data.decode("utf-8", errors="replace")

    try:
        stream = DocumentStream(name=filename, stream=BytesIO(file_data))
        result = converter.convert(stream)
        return result.document.export_to_markdown()
    except Exception as e:
        raise DocumentParseError(f"Failed to parse document '{filename}': {e}") from e