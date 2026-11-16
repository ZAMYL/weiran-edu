// Small deterministic Open XML fixtures, no spreadsheet authoring dependency.
import { mkdirSync, writeFileSync } from 'node:fs';
import { deflateRawSync } from 'node:zlib';

const dimensions = ['算法与数据结构', '代码实现能力', '问题建模能力', '效率意识', '解题策略'];
const rows = [['学生标识', '能力维度', '原始评分', '原始评语']];
for (const [student, scores] of [
  ['示例学生A', [2, 4, 3, 4, 5]],
  ['示例学生B', [4, 3, 4, 3, 4]],
]) {
  dimensions.forEach((name, index) =>
    rows.push([student, name, scores[index], `示例评语：${name}，请替换为真实原始评语。`]),
  );
}
const invalidRows = rows.map((row) => [...row]);
invalidRows[1][2] = 6;
invalidRows[4][2] = null;
const escapeXml = (value) =>
  String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
function workbook(data) {
  return {
    '[Content_Types].xml':
      '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    '_rels/.rels':
      '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml':
      '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels':
      '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    'xl/worksheets/sheet1.xml':
      '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols><col min="1" max="2" width="25" customWidth="1"/><col min="3" max="3" width="15" customWidth="1"/><col min="4" max="4" width="65" customWidth="1"/></cols><sheetData>' +
      data
        .map(
          (row, index) =>
            `<row r="${index + 1}">${row
              .map((value, column) => {
                if (value === null) return '';
                const cell = `${String.fromCharCode(65 + column)}${index + 1}`;
                return typeof value === 'number'
                  ? `<c r="${cell}"><v>${value}</v></c>`
                  : `<c r="${cell}" t="inlineStr"><is><t>${escapeXml(value)}</t></is></c>`;
              })
              .join('')}</row>`,
        )
        .join('') +
      '</sheetData></worksheet>',
  };
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function zip(files) {
  const locals = [],
    central = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const filename = Buffer.from(name),
      raw = Buffer.from(content),
      compressed = deflateRawSync(raw),
      crc = crc32(raw);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(8, 8);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(compressed.length, 18);
    local.writeUInt32LE(raw.length, 22);
    local.writeUInt16LE(filename.length, 26);
    const entry = Buffer.concat([local, filename, compressed]);
    locals.push(entry);
    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(20, 6);
    header.writeUInt16LE(8, 10);
    header.writeUInt32LE(crc, 16);
    header.writeUInt32LE(compressed.length, 20);
    header.writeUInt32LE(raw.length, 24);
    header.writeUInt16LE(filename.length, 28);
    header.writeUInt32LE(offset, 42);
    central.push(Buffer.concat([header, filename]));
    offset += entry.length;
  }
  const directory = Buffer.concat(central),
    end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(central.length, 8);
  end.writeUInt16LE(central.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, directory, end]);
}
mkdirSync(new URL('../tests/fixtures/generated/', import.meta.url), { recursive: true });
for (const [name, data] of [
  ['评价长表模板.xlsx', rows],
  ['评价_待校验示例.xlsx', invalidRows],
]) {
  writeFileSync(
    new URL(`../tests/fixtures/generated/${name}`, import.meta.url),
    zip(workbook(data)),
  );
  console.log(`Generated ${name}`);
}
