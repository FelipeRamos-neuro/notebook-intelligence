// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { JupyterFrontEnd } from '@jupyterlab/application';
import { ReactWidget } from '@jupyterlab/apputils';
import { DocumentRegistry } from '@jupyterlab/docregistry';
import { INotebookModel, NotebookPanel } from '@jupyterlab/notebook';
import { LabIcon, ToolbarButton } from '@jupyterlab/ui-components';
import { IDisposable, DisposableDelegate } from '@lumino/disposable';
import { Widget } from '@lumino/widgets';
import { UUID } from '@lumino/coreutils';
import React from 'react';

import {
  IRunChatCompletionRequest,
  RunChatCompletionType
} from './chat-sidebar';
import {
  buildNotebookGenerationPrompt,
  INotebookGenerationProgressDetail,
  NOTEBOOK_GENERATION_PROGRESS_EVENT
} from './notebook-generation';
import { NotebookGenerationPopover } from './components/notebook-generation-popover';

const TOOLBAR_BUTTON_NAME = 'nbi-generate-notebook';
const TOOLBAR_STATUS_NAME = 'nbi-generate-notebook-status';

interface INotebookGenerationToolbarOptions {
  app: JupyterFrontEnd;
  icon: LabIcon;
  chatSidebarId: string;
}

interface INotebookGenerationPopoverWidgetOptions {
  initialShowInChat: boolean;
  onSubmit: (prompt: string, showInChat: boolean) => void;
  onClose: () => void;
}

class NotebookGenerationPopoverWidget extends ReactWidget {
  constructor(options: INotebookGenerationPopoverWidgetOptions) {
    super();
    this.addClass('nbi-notebook-generation-popover-host');
    this._options = options;
  }

  protected onAfterAttach(): void {
    document.addEventListener('mousedown', this._onDocumentMouseDown, true);
  }

  protected onBeforeDetach(): void {
    document.removeEventListener('mousedown', this._onDocumentMouseDown, true);
  }

  private _onDocumentMouseDown = (event: MouseEvent): void => {
    const target = event.target as Node | null;
    if (target && this.node.contains(target)) {
      return;
    }
    this._options.onClose();
  };

  positionAt(rect: DOMRect): void {
    const popoverWidth = 360;
    const margin = 8;
    let left = rect.left;
    if (left + popoverWidth + margin > window.innerWidth) {
      left = Math.max(margin, window.innerWidth - popoverWidth - margin);
    }
    const top = rect.bottom + 4;
    this.node.style.position = 'fixed';
    this.node.style.left = `${left}px`;
    this.node.style.top = `${top}px`;
    this.node.style.width = `${popoverWidth}px`;
    this.node.style.zIndex = '10000';
  }

  render(): JSX.Element {
    return (
      <NotebookGenerationPopover
        initialShowInChat={this._options.initialShowInChat}
        onSubmit={this._options.onSubmit}
        onClose={this._options.onClose}
      />
    );
  }

  private _options: INotebookGenerationPopoverWidgetOptions;
}

class NotebookGenerationToolbarController {
  constructor(
    options: INotebookGenerationToolbarOptions,
    panel: NotebookPanel
  ) {
    this._app = options.app;
    this._chatSidebarId = options.chatSidebarId;
    this._panel = panel;
  }

  openPopover(button: ToolbarButton): void {
    if (this._popover) {
      this.closePopover();
      return;
    }
    const buttonRect = button.node.getBoundingClientRect();
    this._popover = new NotebookGenerationPopoverWidget({
      initialShowInChat: NotebookGenerationToolbarController._showInChat,
      onSubmit: (prompt, showInChat) => {
        NotebookGenerationToolbarController._showInChat = showInChat;
        this._submitPrompt(prompt, showInChat);
        this.closePopover();
      },
      onClose: () => this.closePopover()
    });
    Widget.attach(this._popover, document.body);
    // ReactWidget renders on update-request; Widget.attach doesn't queue one.
    this._popover.update();
    this._popover.positionAt(buttonRect);
  }

  closePopover(): void {
    if (!this._popover) {
      return;
    }
    this._popover.dispose();
    this._popover = null;
  }

  dispose(): void {
    this.closePopover();
    if (this._activeProgressRequestId) {
      document.removeEventListener(
        NOTEBOOK_GENERATION_PROGRESS_EVENT,
        this._onProgress
      );
      this._activeProgressRequestId = null;
    }
    if (this._statusHideTimer !== null) {
      clearTimeout(this._statusHideTimer);
      this._statusHideTimer = null;
    }
    this._setStatus(null);
  }

  private _submitPrompt(rawPrompt: string, showInChat: boolean): void {
    const prefixedPrompt = buildNotebookGenerationPrompt(rawPrompt);
    const externalRequestId = UUID.uuid4();
    // chatMode and toolSelections are forced by the chat-sidebar handler
    // (NotebookGeneration always needs agent mode + the notebook-edit
    // toolset). Leaving them off the request keeps the toolbar
    // independent of the sidebar's current configuration.
    const request: Partial<IRunChatCompletionRequest> = {
      type: RunChatCompletionType.NotebookGeneration,
      content: prefixedPrompt,
      externalRequestId,
      hideInChat: !showInChat
    };

    if (!showInChat) {
      this._setStatus('Generating notebook…');
      this._activeProgressRequestId = externalRequestId;
      document.addEventListener(
        NOTEBOOK_GENERATION_PROGRESS_EVENT,
        this._onProgress
      );
    }

    document.dispatchEvent(
      new CustomEvent('copilotSidebar:runPrompt', { detail: request })
    );

    if (showInChat) {
      this._app.commands.execute('tabsmenu:activate-by-id', {
        id: this._chatSidebarId
      });
    }
  }

  private _onProgress = (event: Event): void => {
    const detail = (event as CustomEvent<INotebookGenerationProgressDetail>)
      .detail;
    if (!detail || detail.requestId !== this._activeProgressRequestId) {
      return;
    }
    if (!detail.inProgress) {
      document.removeEventListener(
        NOTEBOOK_GENERATION_PROGRESS_EVENT,
        this._onProgress
      );
      this._activeProgressRequestId = null;
      if (detail.error) {
        this._setStatus(`Generation failed: ${detail.error}`);
        this._scheduleStatusHide(4000);
      } else {
        this._setStatus('Notebook generation complete');
        this._scheduleStatusHide(2500);
      }
    }
  };

  private _scheduleStatusHide(delayMs: number): void {
    if (this._statusHideTimer !== null) {
      clearTimeout(this._statusHideTimer);
    }
    this._statusHideTimer = setTimeout(() => {
      this._statusHideTimer = null;
      this._setStatus(null);
    }, delayMs);
  }

  private _setStatus(message: string | null): void {
    if (this._panel.isDisposed) {
      return;
    }
    if (!message) {
      if (this._statusWidget) {
        this._statusWidget.dispose();
        this._statusWidget = null;
      }
      return;
    }
    if (!this._statusWidget) {
      const widget = new Widget();
      widget.addClass('nbi-notebook-generation-status');
      this._panel.toolbar.insertAfter(
        TOOLBAR_BUTTON_NAME,
        TOOLBAR_STATUS_NAME,
        widget
      );
      this._statusWidget = widget;
    }
    this._statusWidget.node.textContent = message;
  }

  // Defaults ON; remembers the user's last choice for the rest of the tab.
  private static _showInChat = true;

  private _app: JupyterFrontEnd;
  private _chatSidebarId: string;
  private _panel: NotebookPanel;
  private _popover: NotebookGenerationPopoverWidget | null = null;
  private _activeProgressRequestId: string | null = null;
  private _statusWidget: Widget | null = null;
  private _statusHideTimer: ReturnType<typeof setTimeout> | null = null;
}

export class NotebookGenerationToolbarExtension
  implements DocumentRegistry.IWidgetExtension<NotebookPanel, INotebookModel>
{
  constructor(options: INotebookGenerationToolbarOptions) {
    this._options = options;
  }

  createNew(
    panel: NotebookPanel,
    _context: DocumentRegistry.IContext<INotebookModel>
  ): IDisposable {
    const controller = new NotebookGenerationToolbarController(
      this._options,
      panel
    );
    const button: ToolbarButton = new ToolbarButton({
      icon: this._options.icon,
      onClick: () => controller.openPopover(button),
      // Notebook generation works in any chat mode — the chat-sidebar
      // handler forces agent mode and the notebook-edit toolset when
      // routing this request (issue #229). The button used to be gated
      // on isInClaudeCodeMode here, but that gate undermined the
      // sidebar-side fix.
      tooltip: 'Update active notebook with AI'
    });
    button.addClass('nbi-notebook-generation-toolbar-button');
    panel.toolbar.insertAfter('cellType', TOOLBAR_BUTTON_NAME, button);
    return new DisposableDelegate(() => {
      controller.dispose();
      button.dispose();
    });
  }

  private _options: INotebookGenerationToolbarOptions;
}
