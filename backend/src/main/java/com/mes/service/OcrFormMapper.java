package com.mes.service;

import com.mes.ocr.OcrGateway.OcrCell;
import com.mes.ocr.OcrGateway.OcrDocument;
import com.mes.ocr.OcrGateway.OcrLine;
import com.mes.ocr.OcrGateway.OcrTable;
import com.mes.ocr.OcrScanResponse;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.DateTimeException;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Component
public class OcrFormMapper {

    private static final Set<String> SYSTEM_FIELDS = Set.of(
            "编号", "单据状态", "状态", "创建人", "创建时间", "更新时间", "修改人", "修改时间",
            "审核人", "审核时间", "审核意见", "审批人", "审批时间", "审批状态", "审批意见",
            "审核日期", "审批日期", "制单人", "制单时间", "关闭时间", "发起人编号", "发起时间",
            "打印次数", "变更人", "变更日期", "审核机器人");
    private static final Set<String> NUMBER_TYPES = Set.of(
            "整数", "小数", "数字", "金额", "数量", "number", "integer", "decimal", "double", "float", "long");
    private static final Set<String> BOOLEAN_TYPES = Set.of("是否", "布尔", "boolean", "bool");
    private static final Set<String> DATE_TYPES = Set.of("日期", "date", "datetime", "日期时间");
    private static final Pattern NUMBER = Pattern.compile(
            "^(?:RMB|CNY|[￥¥$])?\\s*([-+]?\\d[\\d,，]*(?:\\.\\d+)?)\\s*(?:%|元|件|个|台|套|kg|KG|千克|吨|米|m|M|小时|天)?$");
    private static final Pattern CHINESE_DATE = Pattern.compile("^(\\d{4})年(\\d{1,2})月(\\d{1,2})日?.*$");
    private static final Pattern SEPARATED_DATE = Pattern.compile("^(\\d{4})[-/.](\\d{1,2})[-/.](\\d{1,2}).*$");

    public OcrScanResponse map(Map<String, Object> panelConfig, OcrDocument document) {
        Set<String> excludedNames = excludedNames(panelConfig);
        List<FieldSpec> headerSpecs = headerFields(panelConfig, excludedNames);
        List<TabSpec> tabSpecs = detailTabs(panelConfig, excludedNames);
        List<String> warnings = new ArrayList<>();
        List<Map<String, Object>> matches = new ArrayList<>();
        Map<String, Object> header = mapHeader(headerSpecs, tabSpecs, document, matches, warnings);
        Map<String, List<Map<String, Object>>> detail = mapTables(tabSpecs, document.tables(), matches, warnings);

        if (header.isEmpty() && detail.values().stream().allMatch(List::isEmpty)) {
            warnings.add("未找到可自动填入的字段，请核对图片内容");
        }
        return new OcrScanResponse(document.requestId(), header, detail, matches, warnings);
    }

    private Map<String, Object> mapHeader(List<FieldSpec> fields, List<TabSpec> tabs, OcrDocument document,
                                           List<Map<String, Object>> matches, List<String> warnings) {
        Map<String, Object> header = new LinkedHashMap<>();
        List<OcrLine> lines = effectiveLines(document);
        Set<String> knownLabels = new LinkedHashSet<>();
        fields.forEach(field -> field.labels().forEach(label -> knownLabels.add(normalizeLabel(label))));
        tabs.forEach(tab -> tab.fields().forEach(field ->
                field.labels().forEach(label -> knownLabels.add(normalizeLabel(label)))));
        fields.stream().sorted(Comparator.comparingInt(this::longestLabel).reversed())
                .forEach(field -> findValue(field.labels(), lines, knownLabels).ifPresent(found -> {
                    Object value = normalize(found.value(), field, warnings);
                    if (value == null || String.valueOf(value).isBlank()) return;
                    header.put(field.name(), value);
                    matches.add(match("header", field.name(), value, null, null, found.confidence()));
                }));
        return header;
    }

    private List<OcrLine> effectiveLines(OcrDocument document) {
        if (!document.lines().isEmpty()) return document.lines();
        List<OcrLine> lines = new ArrayList<>();
        for (String line : document.rawText().split("\\R")) {
            if (!line.isBlank()) lines.add(new OcrLine(line.trim(), null));
        }
        return lines;
    }

    private java.util.Optional<FoundValue> findValue(List<String> labels, List<OcrLine> lines,
                                                      Set<String> knownLabels) {
        for (String label : labels.stream().sorted(Comparator.comparingInt(String::length).reversed()).toList()) {
            java.util.Optional<FoundValue> value = findValue(label, lines, knownLabels);
            if (value.isPresent()) return value;
        }
        return java.util.Optional.empty();
    }

    private java.util.Optional<FoundValue> findValue(String label, List<OcrLine> lines,
                                                      Set<String> knownLabels) {
        Pattern colon = Pattern.compile("^\\s*" + Pattern.quote(label) + "\\s*[:：]\\s*(.+?)\\s*$");
        Pattern spaces = Pattern.compile("^\\s*" + Pattern.quote(label) + "[ \\t　]{1,}(.+?)\\s*$");
        for (int i = 0; i < lines.size(); i++) {
            String text = lines.get(i).text() == null ? "" : lines.get(i).text().trim();
            Matcher colonMatch = colon.matcher(text);
            if (colonMatch.matches()) return java.util.Optional.of(
                    new FoundValue(colonMatch.group(1), lines.get(i).confidence()));
            Matcher spaceMatch = spaces.matcher(text);
            if (spaceMatch.matches()) return java.util.Optional.of(
                    new FoundValue(spaceMatch.group(1), lines.get(i).confidence()));
            if (normalizeLabel(text).equals(normalizeLabel(label))) {
                for (int next = i + 1; next < lines.size(); next++) {
                    String value = lines.get(next).text() == null ? "" : lines.get(next).text().trim();
                    if (value.isBlank()) continue;
                    if (knownLabels.contains(normalizeLabel(value))) break;
                    return java.util.Optional.of(new FoundValue(value, lines.get(next).confidence()));
                }
            }
        }
        return java.util.Optional.empty();
    }

    private Map<String, List<Map<String, Object>>> mapTables(List<TabSpec> tabs, List<OcrTable> tables,
                                                              List<Map<String, Object>> matches,
                                                              List<String> warnings) {
        Map<String, List<Map<String, Object>>> detail = new LinkedHashMap<>();
        tabs.forEach(tab -> detail.put(tab.key(), new ArrayList<>()));
        if (tabs.isEmpty()) return detail;
        if (tables.isEmpty()) {
            warnings.add("未识别到可映射的明细表格");
            return detail;
        }

        int recognizedTables = 0;
        for (OcrTable table : tables) {
            TableMatch best = bestTableMatch(table, tabs);
            if (best == null || best.columns().isEmpty()) continue;
            recognizedTables++;
            List<Integer> rows = table.cells().stream().map(OcrCell::rowStart)
                    .filter(row -> row > best.headerRow()).distinct().sorted().toList();
            for (Integer rowNumber : rows) {
                Map<String, Object> row = new LinkedHashMap<>();
                for (Map.Entry<Integer, FieldSpec> column : best.columns().entrySet()) {
                    String rawValue = valueAt(table.cells(), rowNumber, column.getKey());
                    if (rawValue == null || rawValue.isBlank()) continue;
                    Object value = normalize(rawValue, column.getValue(), warnings);
                    if (value == null || String.valueOf(value).isBlank()) continue;
                    row.put(column.getValue().name(), value);
                }
                if (!row.isEmpty()) {
                    List<Map<String, Object>> target = detail.get(best.tab().key());
                    int index = target.size();
                    target.add(row);
                    row.forEach((field, value) -> matches.add(
                            match("detail", field, value, best.tab().key(), index, null)));
                }
            }
        }
        if (!tables.isEmpty() && recognizedTables == 0) warnings.add("识别到表格，但表头与当前单据明细字段不匹配");
        return detail;
    }

    private TableMatch bestTableMatch(OcrTable table, List<TabSpec> tabs) {
        TableMatch best = null;
        Set<Integer> rows = new LinkedHashSet<>();
        table.cells().forEach(cell -> rows.add(cell.rowStart()));
        for (Integer row : rows) {
            List<OcrCell> headerCells = table.cells().stream().filter(cell -> cell.rowStart() == row).toList();
            for (TabSpec tab : tabs) {
                Map<Integer, FieldSpec> columns = new LinkedHashMap<>();
                for (OcrCell cell : headerCells) {
                    FieldSpec field = fieldForCell(cell.text(), tab.fields());
                    if (field != null) columns.putIfAbsent(cell.columnStart(), field);
                }
                if (!columns.isEmpty() && (best == null || columns.size() > best.columns().size())) {
                    best = new TableMatch(tab, row, columns);
                }
            }
        }
        return best;
    }

    private FieldSpec fieldForCell(String text, List<FieldSpec> fields) {
        String label = normalizeLabel(text);
        if (label.isBlank()) return null;
        return fields.stream().filter(field -> field.labels().stream()
                .anyMatch(alias -> normalizeLabel(alias).equals(label))).findFirst().orElse(null);
    }

    private String valueAt(List<OcrCell> cells, int row, int column) {
        return cells.stream()
                .filter(cell -> cell.rowStart() <= row && cell.rowEnd() >= row
                        && cell.columnStart() <= column && cell.columnEnd() >= column)
                .map(OcrCell::text).filter(value -> value != null && !value.isBlank()).findFirst().orElse(null);
    }

    private Object normalize(String raw, FieldSpec field, List<String> warnings) {
        String value = raw == null ? "" : raw.trim();
        String type = field.type().toLowerCase(Locale.ROOT);
        try {
            if (DATE_TYPES.contains(type)) return normalizeDate(value);
            if (NUMBER_TYPES.contains(type)) return normalizeNumber(value, type);
            if (BOOLEAN_TYPES.contains(type)) return normalizeBoolean(value);
            Object option = normalizeOption(value, field.options());
            return option == null ? value : option;
        } catch (IllegalArgumentException e) {
            warnings.add("字段“" + field.name() + "”的识别值“" + value + "”格式不正确，已跳过");
            return null;
        }
    }

    private Object normalizeDate(String value) {
        Matcher chinese = CHINESE_DATE.matcher(value);
        Matcher separated = SEPARATED_DATE.matcher(value);
        if (chinese.matches()) return date(chinese.group(1), chinese.group(2), chinese.group(3));
        if (separated.matches()) return date(separated.group(1), separated.group(2), separated.group(3));
        try {
            return LocalDate.parse(value, DateTimeFormatter.ISO_LOCAL_DATE).toString();
        } catch (DateTimeException e) {
            throw new IllegalArgumentException("invalid date", e);
        }
    }

    private String date(String year, String month, String day) {
        try {
            return LocalDate.of(Integer.parseInt(year), Integer.parseInt(month), Integer.parseInt(day)).toString();
        } catch (DateTimeException e) {
            throw new IllegalArgumentException("invalid date", e);
        }
    }

    private Object normalizeNumber(String value, String type) {
        Matcher matcher = NUMBER.matcher(value);
        if (!matcher.matches()) throw new IllegalArgumentException("invalid number");
        BigDecimal number = new BigDecimal(matcher.group(1).replace(",", "").replace("，", ""));
        if (Set.of("整数", "integer", "long").contains(type)) {
            try {
                return number.longValueExact();
            } catch (ArithmeticException e) {
                throw new IllegalArgumentException("invalid integer", e);
            }
        }
        return number;
    }

    private Boolean normalizeBoolean(String value) {
        String normalized = normalizeLabel(value).toLowerCase(Locale.ROOT);
        if (Set.of("是", "有", "true", "yes", "y", "1", "√", "启用").contains(normalized)) return true;
        if (Set.of("否", "无", "false", "no", "n", "0", "×", "停用").contains(normalized)) return false;
        throw new IllegalArgumentException("invalid boolean");
    }

    private Object normalizeOption(String value, List<Object> options) {
        String normalized = normalizeLabel(value);
        for (Object option : options) {
            if (option != null && normalizeLabel(String.valueOf(option)).equals(normalized)) return option;
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private List<FieldSpec> headerFields(Map<String, Object> config, Set<String> excludedNames) {
        Object schema = config.get("dataSchema");
        if (!(schema instanceof Map<?, ?> map)) return List.of();
        return fieldSpecs(((Map<String, Object>) map).get("fields"), excludedNames);
    }

    @SuppressWarnings("unchecked")
    private List<TabSpec> detailTabs(Map<String, Object> config, Set<String> excludedNames) {
        Object detail = config.get("detail");
        if (!(detail instanceof Map<?, ?> detailMap)) return List.of();
        Object tabs = ((Map<String, Object>) detailMap).get("tabs");
        if (!(tabs instanceof List<?> list)) return List.of();
        List<TabSpec> specs = new ArrayList<>();
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> raw)) continue;
            Map<String, Object> tab = (Map<String, Object>) raw;
            String key = string(tab.get("key"));
            if (key.isBlank()) continue;
            specs.add(new TabSpec(key, fieldSpecs(tab.get("fields"), excludedNames)));
        }
        return specs;
    }

    @SuppressWarnings("unchecked")
    private List<FieldSpec> fieldSpecs(Object rawFields, Set<String> excludedNames) {
        if (!(rawFields instanceof List<?> fields)) return List.of();
        List<FieldSpec> result = new ArrayList<>();
        for (Object item : fields) {
            if (item instanceof List<?>) {
                result.addAll(fieldSpecs(item, excludedNames));
                continue;
            }
            if (!(item instanceof Map<?, ?> raw)) continue;
            Map<String, Object> field = (Map<String, Object>) raw;
            Object children = field.get("fields");
            if (children instanceof List<?>) result.addAll(fieldSpecs(children, excludedNames));
            String name = string(field.get("dataName"));
            if (name.isBlank() || excludedNames.contains(name) || SYSTEM_FIELDS.contains(name)
                    || truthy(field.get("hidden")) || truthy(field.get("computed")) || truthy(field.get("autoCode"))
                    || truthy(field.get("readonly")) || truthy(field.get("readOnly"))) {
                continue;
            }
            List<Object> options = field.get("options") instanceof List<?> list ? new ArrayList<>(list) : List.of();
            List<String> labels = new ArrayList<>();
            labels.add(name);
            for (String aliasKey : List.of("displayName", "label", "name")) {
                String alias = string(field.get(aliasKey));
                if (!alias.isBlank()) labels.add(alias);
            }
            if (field.get("ocrAliases") instanceof List<?> aliases) {
                for (Object alias : aliases) {
                    String value = string(alias);
                    if (!value.isBlank()) labels.add(value);
                }
            }
            result.add(new FieldSpec(name, labels, string(field.getOrDefault("dataType", "文本")), options));
        }
        Map<String, FieldSpec> unique = new LinkedHashMap<>();
        result.forEach(field -> unique.putIfAbsent(field.name(), field));
        List<FieldSpec> uniqueFields = new ArrayList<>(unique.values());
        Set<String> claimedLabels = new LinkedHashSet<>();
        uniqueFields.forEach(field -> claimedLabels.add(normalizeLabel(field.name())));
        List<FieldSpec> resolved = new ArrayList<>();
        for (FieldSpec field : uniqueFields) {
            List<String> labels = new ArrayList<>();
            labels.add(field.name());
            for (String alias : field.labels()) {
                String normalized = normalizeLabel(alias);
                if (normalized.isBlank() || normalized.equals(normalizeLabel(field.name()))) continue;
                if (claimedLabels.add(normalized)) labels.add(alias);
            }
            resolved.add(new FieldSpec(field.name(), List.copyOf(labels), field.type(), field.options()));
        }
        return resolved;
    }

    @SuppressWarnings("unchecked")
    private Set<String> excludedNames(Map<String, Object> config) {
        Set<String> names = new LinkedHashSet<>(SYSTEM_FIELDS);
        Object metadata = config.get("metadata");
        if (!(metadata instanceof Map<?, ?> raw)) return names;
        Map<String, Object> map = (Map<String, Object>) raw;
        names.add(string(map.get("autoCodeField")));
        if (map.get("panelState") instanceof Map<?, ?> state) names.add(string(state.get("dataName")));
        names.remove("");
        return names;
    }

    private Map<String, Object> match(String target, String field, Object value, String tabKey,
                                      Integer rowIndex, Integer confidence) {
        Map<String, Object> match = new LinkedHashMap<>();
        match.put("target", target);
        match.put("field", field);
        match.put("value", value);
        if (tabKey != null) match.put("tabKey", tabKey);
        if (rowIndex != null) match.put("rowIndex", rowIndex);
        if (confidence != null) match.put("confidence", confidence);
        return match;
    }

    private String normalizeLabel(String value) {
        if (value == null) return "";
        return value.replaceAll("[\\s:：]", "").trim();
    }

    private String string(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private boolean truthy(Object value) {
        return Boolean.TRUE.equals(value) || "true".equalsIgnoreCase(string(value)) || "1".equals(string(value));
    }

    private int longestLabel(FieldSpec field) {
        return field.labels().stream().mapToInt(String::length).max().orElse(field.name().length());
    }

    private record FieldSpec(String name, List<String> labels, String type, List<Object> options) {
    }

    private record TabSpec(String key, List<FieldSpec> fields) {
    }

    private record TableMatch(TabSpec tab, int headerRow, Map<Integer, FieldSpec> columns) {
    }

    private record FoundValue(String value, Integer confidence) {
    }
}
