import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

// Check real downloaded reports against the actual saved/reviewed API response.
export function confirmedReportFindings(saved) {
  assert.ok(Array.isArray(saved.findings) && Array.isArray(saved.discovery_findings));
  const candidateIds = saved.findings
    .filter((f) => f.teacher_review.status === 'confirmed')
    .map((f) => f.candidate.candidate_id);
  const discoveryIds = saved.discovery_findings
    .filter((f) => f.teacher_review.status === 'confirmed')
    .map((f) => f.discovery.discovery_id);
  assert.equal(new Set(candidateIds).size, candidateIds.length);
  assert.equal(new Set(discoveryIds).size, discoveryIds.length);
  return {
    candidateIds,
    discoveryIds,
    confirmedFindingCount: candidateIds.length + discoveryIds.length,
  };
}
export function verifyReportArtifact({ format, bytes, destination, python, env, reference }) {
  const confirmed = confirmedReportFindings(reference.savedResult);
  assert.deepEqual(reference.discoveryIds, confirmed.discoveryIds);
  assert.equal(reference.resultId, reference.savedResult.result_id);
  assert.equal(reference.version, reference.savedResult.version);
  let rows, sheets, discoveryReferences;
  if (format === 'md') {
    const text = bytes.toString('utf8');
    assert.match(text, /^#/);
    discoveryReferences = Array.from(
      text.matchAll(/^主动发现 \d+：([a-f0-9]{24})：/gm),
      (match) => match[1],
    );
    rows = text
      .split(/\r?\n/)
      .filter((line) => line.startsWith('|'))
      .map((line) =>
        line
          .split('|')
          .slice(1, -1)
          .map((cell) => cell.trim()),
      );
    for (const match of text.matchAll(/^教师已确认发现数：([0-9]+)\s*$/gm))
      rows.push(['教师已确认发现数', match[1]]);
    sheets = [];
  } else {
    assert.equal(format, 'xlsx');
    assert.equal(bytes.subarray(0, 4).toString('hex'), '504b0304');
    const reopened = JSON.parse(
      execFileSync(
        python,
        [
          '-c',
          "import json,sys; from openpyxl import load_workbook; w=load_workbook(sys.argv[1],read_only=True,data_only=True); rows=[[str(v) if v is not None else '' for v in row] for s in w for row in s.iter_rows(values_only=True)]; print(json.dumps({'sheets':w.sheetnames,'rows':rows},ensure_ascii=False)); w.close()",
          destination,
        ],
        { env: { ...env, PYTHONUTF8: '1' }, encoding: 'utf8', windowsHide: true },
      ),
    );
    rows = reopened.rows;
    sheets = reopened.sheets;
    assert.ok(sheets.length >= 4);
  }
  const expected = [
    ['测评记录数', reference.studentCount],
    ['教师已确认发现数', confirmed.confirmedFindingCount],
    ['测评批次引用', reference.batchId],
    ['数据快照引用', reference.snapshotId],
    ['保存结果引用', reference.resultId],
    ['教师核验版本', reference.version],
  ];
  for (const [label, value] of expected) {
    const actual = rows.filter((row) => row[0] === label);
    assert.ok(
      actual.length > 0 && actual.every((row) => row[1] === String(value)),
      `${format}: mismatched ${label}`,
    );
  }
  for (const [label, value] of reference.factRows ?? [])
    assert.ok(
      rows.some((row) => row[0] === label && row[1] === String(value)),
      `${format}: mismatched discovery fact ${label}`,
    );
  const cells = rows.flat();
  discoveryReferences ??= [...new Set(cells.filter((cell) => /^[a-f0-9]{24}$/.test(cell)))];
  assert.equal(
    discoveryReferences.length,
    reference.discoveryIds.length,
    `${format}: unexpected discovery references`,
  );
  for (const id of reference.discoveryIds)
    assert.ok(discoveryReferences.includes(id), `${format}: missing confirmed discovery reference`);
  assert.doesNotMatch(cells.join('\n'), /合成私密姓名|未审核合成私密评语|合成私密依据/);
  return {
    sheets,
    verifiedMetadataFields: expected.length,
    confirmedDiscoveryCount: reference.discoveryIds.length,
    confirmedFindingCount: confirmed.confirmedFindingCount,
    verifiedFactRows: reference.factRows?.length ?? 0,
  };
}
