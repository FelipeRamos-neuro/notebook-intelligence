// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// The mention menu renders into document.body, so anything that leaves it
// open outlives the cell that opened it and paints over unrelated panels.
import { EditorView } from '@codemirror/view';
import { NBIAPI } from '../../src/api';
import { setChatbookMentionsEnabled } from '../../src/chatbook-mentions';

const menu = () => document.querySelector('.nbi-chatbook-mention-menu');
const settle = () => new Promise(resolve => setTimeout(resolve, 260));

describe('chatbook mention menu lifecycle', () => {
  let view: EditorView;

  beforeEach(() => {
    jest.spyOn(NBIAPI, 'listChatbookMentions').mockResolvedValue({
      items: [
        {
          label: 'notes.md',
          value: 'file:notes.md',
          kind: 'file',
          hasChildren: false
        }
      ],
      breadcrumbs: []
    });
    view = new EditorView({ doc: '', parent: document.body });
    // jsdom has no layout, so CodeMirror's own measurement throws and the
    // menu's error path would close it before the test could look.
    jest.spyOn(view, 'coordsAtPos').mockReturnValue({
      left: 0,
      right: 0,
      top: 0,
      bottom: 0
    });
    setChatbookMentionsEnabled(view, true, 'nb.ipynb');
  });

  afterEach(() => {
    view.destroy();
    jest.restoreAllMocks();
  });

  async function openMenu(): Promise<void> {
    view.focus();
    view.dispatch({
      changes: { from: 0, insert: 'summarize @' },
      selection: { anchor: 11 }
    });
    await settle();
  }

  it('opens for a mention trigger', async () => {
    await openMenu();
    expect(menu()).not.toBeNull();
    // Selection runs through the editor, so the body-parented options must
    // not become tab stops of their own at the end of the document.
    const options = [
      ...document.querySelectorAll<HTMLButtonElement>(
        '.nbi-chatbook-mention-option'
      )
    ];
    expect(options.length).toBeGreaterThan(0);
    expect(options.every(option => option.tabIndex === -1)).toBe(true);
  });

  it('closes when the editor loses focus', async () => {
    await openMenu();
    expect(menu()).not.toBeNull();

    view.contentDOM.blur();
    await settle();

    expect(menu()).toBeNull();
    // Assistive technology reads these off the editor, so a menu that is gone
    // from the DOM but still referenced is still open as far as AT is told.
    expect(
      document.querySelector('[aria-controls],[aria-activedescendant]')
    ).toBeNull();
  });

  it('stays open when only the browser window loses focus', async () => {
    await openMenu();

    // A window blur fires blur on the focused node without moving the caret,
    // so someone who alt-tabbed to look a filename up keeps their menu.
    jest.spyOn(document, 'hasFocus').mockReturnValue(false);
    view.contentDOM.dispatchEvent(new FocusEvent('blur'));
    await settle();

    expect(view.hasFocus).toBe(false);
    expect(menu()).not.toBeNull();
  });

  it('closes when the caret leaves while the window is unfocused', async () => {
    await openMenu();
    // Alt-tab away: CodeMirror now reports itself unfocused, so a later caret
    // move produces no focus change of its own to notice.
    jest.spyOn(document, 'hasFocus').mockReturnValue(false);
    view.contentDOM.dispatchEvent(new FocusEvent('blur'));
    await settle();
    expect(menu()).not.toBeNull();

    const elsewhere = document.createElement('input');
    document.body.appendChild(elsewhere);
    elsewhere.focus();
    view.contentDOM.dispatchEvent(new FocusEvent('blur'));
    await settle();

    expect(menu()).toBeNull();
    elsewhere.remove();
  });

  it('stays closed when the cell is edited with the caret elsewhere', async () => {
    await openMenu();
    view.contentDOM.blur();
    await settle();
    expect(menu()).toBeNull();

    // A mode flip rewrites the cell source while focus sits on the badge that
    // flipped it. Reopening here would leave a menu no blur can close.
    view.dispatch({ changes: { from: 11, insert: 'n' } });
    await settle();

    expect(menu()).toBeNull();
  });

  it('does not open for an unfocused editor when mentions are re-enabled', async () => {
    setChatbookMentionsEnabled(view, false, 'nb.ipynb');
    view.dispatch({
      changes: { from: 0, insert: 'summarize @' },
      selection: { anchor: 11 }
    });

    setChatbookMentionsEnabled(view, true, 'nb.ipynb');
    await settle();

    expect(menu()).toBeNull();
    expect(NBIAPI.listChatbookMentions).not.toHaveBeenCalled();
  });

  it('keeps the caret in the editor when the menu itself is clicked', async () => {
    await openMenu();
    const target = menu() as HTMLElement;

    const event = new MouseEvent('mousedown', {
      bubbles: true,
      cancelable: true
    });
    target.dispatchEvent(event);

    // A click that lands on the container rather than an option must not move
    // focus, or it would dismiss the menu the user was aiming at.
    expect(event.defaultPrevented).toBe(true);
    expect(menu()).not.toBeNull();
  });

  it('is gone once the editor is destroyed', async () => {
    await openMenu();
    view.destroy();
    expect(menu()).toBeNull();
  });
});
