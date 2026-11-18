package com.mes.ocr;

import java.io.InputStream;
import java.util.List;

/** Cloud OCR boundary. The service owns validation and schema mapping. */
public interface OcrGateway {

    OcrDocument recognize(InputStream image) throws OcrGatewayException;

    record OcrDocument(String requestId, String rawText, List<OcrLine> lines, List<OcrTable> tables) {
        public OcrDocument {
            rawText = rawText == null ? "" : rawText;
            lines = lines == null ? List.of() : List.copyOf(lines);
            tables = tables == null ? List.of() : List.copyOf(tables);
        }
    }

    record OcrLine(String text, Integer confidence) {
    }

    record OcrTable(List<OcrCell> cells) {
        public OcrTable {
            cells = cells == null ? List.of() : List.copyOf(cells);
        }
    }

    record OcrCell(int rowStart, int rowEnd, int columnStart, int columnEnd, String text) {
    }
}
