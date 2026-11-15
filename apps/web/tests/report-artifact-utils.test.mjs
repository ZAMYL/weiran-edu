import assert from 'node:assert/strict';
import { test } from 'node:test';
import { confirmedReportFindings, verifyReportArtifact } from './report-artifact-utils.mjs';

test('counts both saved finding families, excluding pending and dismissed', () => {
  const statuses = ['confirmed', 'pending', 'dismissed', 'confirmed'];
  const saved = {
    result_id: 'saved',
    version: 3,
    findings: statuses.map((status, i) => ({
      candidate: { candidate_id: `ordinary-${i}` },
      teacher_review: { status },
    })),
    discovery_findings: statuses
      .slice(0, 3)
      .map((status, i) => ({
        discovery: { discovery_id: String(i).padStart(24, 'a') },
        teacher_review: { status },
      })),
  };
  const result = confirmedReportFindings(saved);
  assert.equal(result.confirmedFindingCount, 3);
  assert.equal(result.candidateIds.length, 2);
  const text = `# report\n|测评记录数|17|\n|教师已确认发现数|3|\n|测评批次引用|batch|\n|数据快照引用|snapshot|\n|保存结果引用|saved|\n|教师核验版本|3|\n主动发现 1：${result.discoveryIds[0]}：detail`;
  const reference = {
    savedResult: saved,
    studentCount: 17,
    discoveryIds: result.discoveryIds,
    batchId: 'batch',
    snapshotId: 'snapshot',
    resultId: 'saved',
    version: 3,
  };
  assert.equal(
    verifyReportArtifact({ format: 'md', bytes: Buffer.from(text), reference })
      .confirmedFindingCount,
    3,
  );
  assert.throws(
    () =>
      verifyReportArtifact({
        format: 'md',
        bytes: Buffer.from(text.replace('|教师已确认发现数|3|', '|教师已确认发现数|1|')),
        reference,
      }),
    /教师已确认发现数/,
  );
});

test('zero confirmed findings and duplicate persisted identities are checked', () => {
  assert.equal(
    confirmedReportFindings({ findings: [], discovery_findings: [] }).confirmedFindingCount,
    0,
  );
  const item = { candidate: { candidate_id: 'same' }, teacher_review: { status: 'confirmed' } };
  assert.throws(() => confirmedReportFindings({ findings: [item, item], discovery_findings: [] }));
});
