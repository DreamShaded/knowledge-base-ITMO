"""Abstract base for PDF parsers."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.di.ports import ParsedBook


class PdfParserBase(ABC):
    @abstractmethod
    async def parse(self, path: str) -> ParsedBook:
        """Parse PDF at path and return structured book representation."""
        ...

    @classmethod
    def is_available(cls) -> bool:
        """Return True if the parser's dependencies are installed."""
        return True
