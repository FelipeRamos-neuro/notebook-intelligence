// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// The Claude tab is the only settings tab with an icon, and the icon was an
// unnamed image node inside the tab.
jest.mock(
  '@jupyterlab/apputils',
  () => ({
    ReactWidget: class {},
    Dialog: class {},
    showDialog: () => undefined,
    Notification: { info: () => undefined }
  }),
  { virtual: true }
);

import React from 'react';
import { render, screen } from '@testing-library/react';

import { SettingsPanelTabsComponent } from '../../src/components/settings-panel';

function renderTabs() {
  render(
    <SettingsPanelTabsComponent
      tabs={[
        { id: 'general', label: 'General' },
        {
          id: 'claude',
          label: 'Claude',
          icon: () => (
            <span
              className="claude-icon"
              dangerouslySetInnerHTML={{ __html: '<svg><path /></svg>' }}
            ></span>
          )
        }
      ]}
      activeTab="general"
      onTabSelected={() => undefined}
    />
  );
}

describe('settings tabs', () => {
  it('leaves no exposed svg inside any tab', () => {
    renderTabs();

    // The invariant, rather than the markup that currently implements it: no
    // tab may contain an svg that the accessibility tree can see.
    for (const tab of screen.getAllByRole('tab')) {
      for (const svg of tab.querySelectorAll('svg')) {
        expect(svg.closest('[aria-hidden="true"]')).not.toBeNull();
      }
    }
  });

  it('still renders the icon it hides', () => {
    renderTabs();

    const claudeTab = screen.getByRole('tab', { name: 'Claude' });
    expect(claudeTab.querySelector('svg')).not.toBeNull();
  });

  it('names every tab from its label', () => {
    renderTabs();

    expect(screen.getByRole('tab', { name: 'Claude' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'General' })).toBeTruthy();
  });
});
