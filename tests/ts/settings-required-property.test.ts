// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// A required provider property left blank persists as an empty string and the
// next request carries that field empty, so the field has to show it.

import { isBlankRequiredProperty } from '../../src/components/settings-panel';

describe('isBlankRequiredProperty', () => {
  it('flags a required property with no value', () => {
    expect(isBlankRequiredProperty({ value: '' })).toBe(true);
    expect(isBlankRequiredProperty({ value: '   ' })).toBe(true);
    expect(isBlankRequiredProperty({})).toBe(true);
  });

  it('accepts a required property that has a value', () => {
    expect(isBlankRequiredProperty({ value: 'gpt-4o' })).toBe(false);
  });

  it('leaves an optional property alone whether or not it is set', () => {
    expect(isBlankRequiredProperty({ value: '', optional: true })).toBe(false);
    expect(
      isBlankRequiredProperty({ value: 'http://host/v1', optional: true })
    ).toBe(false);
  });
});
