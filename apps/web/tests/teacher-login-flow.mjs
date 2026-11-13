import assert from 'node:assert/strict';
import path from 'node:path';

export async function teacherLoginFlow({
  page,
  origin,
  artifacts,
  temporary,
  teacher,
  admin,
  testAccount,
}) {
  const dialog = page.getByRole('dialog', { name: '教师登录', exact: true });
  const upload = page.getByRole('button', { name: '上传测评资料', exact: true });
  const nav = page.getByRole('link', { name: '分析报告', exact: true });
  const logout = page.getByRole('button', { name: '结束会话', exact: true });
  const request = (url, method, status) =>
    page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === url &&
        response.request().method() === method &&
        response.status() === status,
    );
  const shot = (name) =>
    page.screenshot({ path: path.join(artifacts, name + '.png'), animations: 'disabled' });

  async function login(form, password, status) {
    await form.getByLabel('账号', { exact: true }).fill(testAccount.account);
    await form.getByLabel('密码', { exact: true }).fill(password);
    const [reply] = await Promise.all([
      page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === '/api/teacher-session' &&
          response.request().method() === 'POST',
      ),
      form.getByRole('button', { name: '登录', exact: true }).click(),
    ]);
    assert.equal(reply.status(), status, 'Account login HTTP status');
  }

  await page.goto(origin + '/');
  await page.locator('header').getByRole('button', { name: '登录', exact: true }).click();
  await dialog.waitFor();
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  await nav.click();
  await dialog.waitFor();
  assert.equal(new URL(page.url()).pathname, '/insights');
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  await upload.click();
  await dialog.waitFor();
  assert.equal(await page.locator('input[type=file]').count(), 0);
  assert.equal(
    await page.locator('.ant-modal-mask').evaluate((el) => getComputedStyle(el).backdropFilter),
    'blur(7px)',
  );
  await shot('login-modal-desktop');
  await login(dialog, 'wrong-password', 401);
  await dialog.getByText('账号或密码不正确，或账户已失效。').waitFor();
  assert.equal(await dialog.getByLabel('密码', { exact: true }).inputValue(), '');
  await login(dialog, testAccount.password, 200);
  await dialog.waitFor({ state: 'hidden' });
  await page.locator('input[type=file]').waitFor({ state: 'attached' });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await shot('signed-in-upload-mobile');
  const cookie = (await page.context().cookies()).find(
    (value) => value.name === 'weiran_teacher_test_session',
  );
  assert.ok(cookie?.httpOnly);
  assert.equal(cookie.sameSite, 'Strict');
  assert.equal(cookie.path, '/api');
  const me = await page.request.get(origin + '/api/edu/me');
  assert.equal(me.status(), 200);
  assert.deepEqual(await me.json(), { teacher_id: teacher });
  assert.equal(
    await page.evaluate(() =>
      JSON.stringify([localStorage, sessionStorage]).includes('local-synthetic-password'),
    ),
    false,
  );

  // A cookie-backed write reaches FastAPI after the originally requested upload opens.
  const preview = request('/api/edu/imports/preview', 'POST', 201);
  await page.locator('input[type=file]').setInputFiles(path.join(temporary, 'empty.xlsx'));
  await preview;
  await page.getByRole('heading', { name: '文件检查结果', exact: true }).waitFor();
  await page.reload();
  await logout.waitFor();
  await page.goto(origin + '/reports');
  await page.getByRole('heading', { name: '分析报告', exact: true }).waitFor();
  await logout.waitFor();
  const signedOut = request('/api/teacher-session', 'DELETE', 204);
  await logout.click();
  await signedOut;
  await page.reload();
  await page.getByRole('heading', { name: '连接账户，查看测评', exact: true }).waitFor();
  assert.equal(
    (await page.context().cookies()).some((value) => value.name === 'weiran_teacher_test_session'),
    false,
  );
  assert.equal((await page.request.get(origin + '/api/edu/me')).status(), 401);

  await page.goto(origin + '/login');
  await page.getByRole('heading', { name: '教师登录', exact: true }).waitFor();
  await shot('login-page-mobile');
  await login(page, testAccount.password, 200);
  await page.waitForURL('**/insights');
  await logout.waitFor();
  admin('token-revoke', '--teacher-id', teacher);
  const revoked = request('/api/edu/me', 'GET', 401);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await revoked;
  await page.getByText('登录已失效，请重新登录。', { exact: true }).waitFor();
  assert.equal(await page.locator('.live-workspace').count(), 0);
  await shot('session-expired-mobile');
  return [
    'guest action opens login / Escape cancels / invalid password 401 / login 200 and resumes upload',
    'HttpOnly cookie / real FastAPI upload preview / refresh and protected route restore',
    'logout 204 clears cookie / reload stays logged out / direct login succeeds / revoked identity clears on focus',
  ];
}
