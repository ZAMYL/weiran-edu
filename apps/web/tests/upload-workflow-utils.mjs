import assert from 'node:assert/strict';

export async function renameImport(page, name) {
  const field = page.getByLabel('测评名称', { exact: true });
  await field.waitFor();
  if ((await field.inputValue()) === name) return;
  const response = page.waitForResponse(
    (r) => r.request().method() === 'PATCH' && /\/imports\/[^/?]+$/.test(new URL(r.url()).pathname),
  );
  await field.fill(name);
  await field.press('Tab');
  const result = await response;
  assert.equal(result.status(), 200, 'the edited name must be saved by the real API');
  const updated = await result.json();
  await page.waitForFunction(
    () => document.querySelector('.upload-workflow')?.getAttribute('aria-busy') === 'false',
  );
  assert.equal(await field.inputValue(), updated.import.title);
  return updated;
}
