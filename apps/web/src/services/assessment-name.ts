export function assessmentNameFromFilename(filename: string): string {
  return (
    filename
      .replace(/^.*[/\\]/, '')
      .replace(/\.[^./\\]+$/, '')
      .replace(/[\u0000-\u001f\u007f]/g, ' ')
      .trim()
      .slice(0, 100) || '未命名测评'
  );
}
