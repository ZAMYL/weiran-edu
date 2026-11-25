const MAX_ZIP_FILES = 20;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export type UploadClassification =
  | { kind: 'scores'; file: File }
  | { kind: 'answer_sheets'; files: File[] }
  | { kind: 'invalid'; message: string };

export function classifyUploadFiles(files: readonly File[]): UploadClassification {
  if (!files.length) {
    return { kind: 'invalid', message: '请选择要上传的文件。' };
  }
  const hasXlsx = files.some((file) => /\.xlsx$/i.test(file.name));
  const hasZip = files.some((file) => /\.zip$/i.test(file.name));
  const allXlsx = files.every((file) => /\.xlsx$/i.test(file.name));
  const allZip = files.every((file) => /\.zip$/i.test(file.name));

  if (hasXlsx && hasZip) {
    return {
      kind: 'invalid',
      message: '不能同时混选测评表格（.xlsx）和编程答题卡（.zip），请分批上传。',
    };
  }
  if (!allXlsx && !allZip) {
    return {
      kind: 'invalid',
      message: '目前支持 .xlsx 测评表或 .zip 编程答题卡，请重新选择。',
    };
  }

  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  if (allXlsx) {
    if (files.length > 1) {
      return { kind: 'invalid', message: '测评数据每次请上传单个 .xlsx 文件。' };
    }
    if (totalBytes > MAX_UPLOAD_BYTES) {
      return { kind: 'invalid', message: '文件超过 10 MB，请缩小后重新上传。' };
    }
    return { kind: 'scores', file: files[0]! };
  }

  if (files.length > MAX_ZIP_FILES) {
    return { kind: 'invalid', message: '编程答题卡单次最多选择 20 个 ZIP 文件。' };
  }
  if (totalBytes > MAX_UPLOAD_BYTES) {
    return { kind: 'invalid', message: '文件总大小超过 10 MB，请缩小后重新上传。' };
  }
  return { kind: 'answer_sheets', files: [...files] };
}
