import { describe, expect, it } from 'vitest';
import { assessmentNameFromFilename } from './assessment-name';

describe('assessmentNameFromFilename', () => {
  it('strips any single trailing extension while preserving stem dots', () => {
    expect(assessmentNameFromFilename('custom-name-sample.zip')).toBe('custom-name-sample');
    expect(assessmentNameFromFilename('期中评价.xlsx')).toBe('期中评价');
    expect(assessmentNameFromFilename('grade10.v2.html')).toBe('grade10.v2');
    expect(assessmentNameFromFilename('no_ext')).toBe('no_ext');
    expect(assessmentNameFromFilename('.zip')).toBe('未命名测评');
  });
});
