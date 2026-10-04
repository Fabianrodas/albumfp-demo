import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Confirm } from '../../../core/services/confirm';
import { MediaComments } from './media-comments';

const comment = (id: number, body: string, own = false, edited = false) => ({
  id, asset_id: 7, body, created_at: '2026-09-20T10:00:00', updated_at: edited ? '2026-09-21T10:00:00' : null, edited,
  author: { id: own ? 1 : 2, username: own ? 'ana' : 'bea', full_name: own ? 'Ana' : 'Bea', has_avatar: false },
  is_own: own,
});
const page = (data: unknown[], p = 1, totalPages = 1, total = data.length) =>
  ({ data, message: 'ok', meta: { pagination: { page: p, per_page: 20, total, total_pages: totalPages } } });

describe('MediaComments (F05)', () => {
  let http: HttpTestingController;
  afterEach(() => http?.verify());

  function create(shareToken = '') {
    TestBed.configureTestingModule({ imports: [MediaComments], providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(MediaComments);
    fixture.componentRef.setInput('mediaId', 7);
    fixture.componentRef.setInput('shareToken', shareToken);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement, cmp: fixture.componentInstance };
  }
  const listReq = (url = '/api/media/7/comments') => http.expectOne(r => r.url === url && r.method === 'GET');
  async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
    fixture.detectChanges(); await fixture.whenStable(); fixture.detectChanges();
  }

  it('shows loading, then an honest empty state with a composer', async () => {
    const { fixture, el } = create();
    expect(el.textContent).toContain('Cargando comentarios');
    listReq().flush(page([]));
    await settle(fixture);
    expect(el.textContent).toContain('Todavía no hay comentarios');
    expect(el.querySelector('#comment-draft')).not.toBeNull();
  });

  it('surfaces a failed load with a retry', async () => {
    const { fixture, el } = create();
    listReq().flush({ message: 'Caído' }, { status: 503, statusText: 'x' });
    await settle(fixture);
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('Caído');
    (el.querySelector('.comments__error button') as HTMLButtonElement).click();
    listReq().flush(page([]));
  });

  it('renders bodies as plain text, keeping line breaks and never interpreting markup', async () => {
    const { fixture, el } = create();
    const xss = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    listReq().flush(page([comment(1, 'línea 1\nlínea 2'), comment(2, xss), comment(3, 'x'.repeat(2000))]));
    await settle(fixture);
    const bodies = [...el.querySelectorAll('.comment__body')] as HTMLElement[];
    expect(bodies[0].textContent).toBe('línea 1\nlínea 2');
    expect(getComputedStyle(bodies[0]).whiteSpace).toBe('pre-wrap');
    expect(bodies[1].textContent).toBe(xss);
    expect(el.querySelector('.comment__body img, .comment__body script')).toBeNull();
    expect(bodies[2].textContent?.length).toBe(2000);
  });

  it('offers edit/delete only on own comments and shows the edited marker', async () => {
    const { fixture, el } = create();
    listReq().flush(page([comment(1, 'mío', true, true), comment(2, 'ajeno')]));
    await settle(fixture);
    const items = [...el.querySelectorAll('.comment')];
    expect(items[0].querySelectorAll('.comment__tools button').length).toBe(2);
    expect(items[0].textContent).toContain('(editado)');
    expect(items[1].querySelector('.comment__tools')).toBeNull();
  });

  it('creates a comment and appends it, trimming the body', async () => {
    const { fixture, el, cmp } = create();
    listReq().flush(page([]));
    await settle(fixture);
    cmp.draft = '  hola\ncon salto  ';
    cmp.submit();
    const post = http.expectOne(r => r.url === '/api/media/7/comments' && r.method === 'POST');
    expect(post.request.body).toEqual({ body: 'hola\ncon salto' });
    post.flush({ data: comment(9, 'hola\ncon salto', true), message: 'ok' }, { status: 201, statusText: 'Created' });
    await settle(fixture);
    expect(cmp.draft).toBe('');
    expect(el.querySelector('.comment__body')?.textContent).toBe('hola\ncon salto');
  });

  it('keeps the server message when posting fails', async () => {
    const { fixture, el, cmp } = create();
    listReq().flush(page([]));
    cmp.draft = 'algo';
    cmp.submit();
    http.expectOne(r => r.method === 'POST').flush({ message: 'Estás comentando muy deprisa.' }, { status: 429, statusText: 'x' });
    await settle(fixture);
    expect(el.querySelector('.form-error')?.textContent).toContain('muy deprisa');
  });

  it('edits and deletes own comments (delete asks first)', async () => {
    const { fixture, el, cmp } = create();
    listReq().flush(page([comment(1, 'antes', true)]));
    await settle(fixture);
    cmp.startEdit(cmp.comments()[0]);
    cmp.editDraft = 'después';
    cmp.saveEdit(cmp.comments()[0]);
    http.expectOne(r => r.url === '/api/comments/1' && r.method === 'PATCH')
      .flush({ data: comment(1, 'después', true, true), message: 'ok' });
    await settle(fixture);
    expect(el.querySelector('.comment__body')?.textContent).toBe('después');

    const ask = vi.spyOn(TestBed.inject(Confirm), 'ask').mockResolvedValue(true);
    await cmp.remove(cmp.comments()[0]);
    expect(ask).toHaveBeenCalled();
    http.expectOne(r => r.url === '/api/comments/1' && r.method === 'DELETE').flush({ message: 'ok' });
    await settle(fixture);
    expect(el.querySelectorAll('.comment').length).toBe(0);
  });

  it('loads more pages without duplicates', async () => {
    const { fixture, el } = create();
    listReq().flush(page([comment(1, 'a'), comment(2, 'b')], 1, 2, 3));
    await settle(fixture);
    (el.querySelector('.comments__more') as HTMLButtonElement).click();
    const second = listReq();
    expect(second.request.params.get('page')).toBe('2');
    second.flush(page([comment(2, 'b'), comment(3, 'c')], 2, 2, 3));
    await settle(fixture);
    expect([...el.querySelectorAll('.comment__body')].map(b => b.textContent)).toEqual(['a', 'b', 'c']);
    expect(el.querySelector('.comments__more')).toBeNull();
  });

  it('is read-only for an anonymous public-share visitor', async () => {
    const { fixture, el } = create('tok-123');
    listReq('/api/shared/tok-123/media/7/comments').flush(page([{ ...comment(1, 'visible'), is_own: undefined }]));
    await settle(fixture);
    expect(el.textContent).toContain('visible');
    expect(el.querySelector('#comment-draft')).toBeNull();
    expect(el.querySelector('.comment__tools')).toBeNull();
    expect(el.textContent).toContain('Solo las cuentas con acceso');
  });

  it('does not infer anything from album capabilities: a read-only collaborator gets the composer', async () => {
    // El componente no recibe rol ni capacidades: el servidor decide al publicar.
    const { fixture, el } = create();
    listReq().flush(page([]));
    await settle(fixture);
    expect(el.querySelector('#comment-draft')).not.toBeNull();
  });
});
