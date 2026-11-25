import { describe, expect, it } from 'vitest';
import { classifyUploadFiles } from './upload-classification';

function makeFile(name: string, size = 1024) {
  return new File([new Uint8Array(size)], name);
}

describe('upload file auto-classification', () => {
  it('detects single .xlsx file as scores assessment data', () => {
    const file = makeFile('期中测评.XLSX', 2048);
    expect(classifyUploadFiles([file])).toEqual({ kind: 'scores', file });
  });

  it('detects one or multiple .zip files as programming answer sheets', () => {
    const files = [makeFile('A组.zip', 1024), makeFile('B组.ZIP', 2048)];
    expect(classifyUploadFiles(files)).toEqual({ kind: 'answer_sheets', files });
  });

  it('rejects empty, mixed, unsupported, over-count, and over-size selections', () => {
    expect(classifyUploadFiles([])).toEqual({
      kind: 'invalid',
      message: '请选择要上传的文件。',
    });
    expect(classifyUploadFiles([makeFile('a.xlsx'), makeFile('b.zip')])).toEqual({
      kind: 'invalid',
      message: '不能同时混选测评表格（.xlsx）和编程答题卡（.zip），请分批上传。',
    });
    expect(classifyUploadFiles([makeFile('notes.pdf')])).toEqual({
      kind: 'invalid',
      message: '目前支持 .xlsx 测评表或 .zip 编程答题卡，请重新选择。',
    });
    expect(classifyUploadFiles([makeFile('a.xlsx'), makeFile('b.xlsx')])).toEqual({
      kind: 'invalid',
      message: '测评数据每次请上传单个 .xlsx 文件。',
    });
    const tooManyZips = Array.from({ length: 21 }, (_, idx) => makeFile(`f${idx}.zip`, 16));
    expect(classifyUploadFiles(tooManyZips)).toEqual({
      kind: 'invalid',
      message: '编程答题卡单次最多选择 20 个 ZIP 文件。',
    });
    expect(classifyUploadFiles([makeFile('huge.xlsx', 10 * 1024 * 1024 + 1)])).toEqual({
      kind: 'invalid',
      message: '文件超过 10 MB，请缩小后重新上传。',
    });
    expect(
      classifyUploadFiles([
        makeFile('p1.zip', 6 * 1024 * 1024),
        makeFile('p2.zip', 5 * 1024 * 1024),
      ]),
    ).toEqual({
      kind: 'invalid',
      message: '文件总大小超过 10 MB，请缩小后重新上传。',
    });
  });
});
