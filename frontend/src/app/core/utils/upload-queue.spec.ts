import { Observable, Subject } from 'rxjs';
import { UploadQueue, UploadQueueEvent } from './upload-queue';

type Payload = { file: File; title: string };

const file = (name: string) => new File([name], name, { type: 'image/jpeg' });

describe('UploadQueue', () => {
  it('never exceeds the configured concurrency', () => {
    const requests = new Map<number, Subject<UploadQueueEvent<number>>>();
    let active = 0;
    let peak = 0;
    const queue = new UploadQueue<Payload, number>(item => {
      active++;
      peak = Math.max(peak, active);
      const request = new Subject<UploadQueueEvent<number>>();
      requests.set(item.id, request);
      return new Observable(subscriber => {
        const subscription = request.subscribe(subscriber);
        return () => { active--; subscription.unsubscribe(); };
      });
    }, 3);
    queue.add(Array.from({ length: 5 }, (_, index) => ({ file: file(`${index}.jpg`), title: `${index}` })));

    queue.start(item => ({ file: item.file, title: item.title }));
    expect(requests.size).toBe(3);
    expect(peak).toBe(3);

    requests.get(1)!.next({ type: 'success', result: 1 });
    expect(requests.size).toBe(4);
    expect(peak).toBe(3);
  });

  it('reports byte progress, processing, and success per item', () => {
    const request = new Subject<UploadQueueEvent<number>>();
    const queue = new UploadQueue<Payload, number>(() => request, 2);
    const [id] = queue.add([{ file: file('uno.jpg'), title: 'Uno' }]);
    queue.start(item => ({ file: item.file, title: item.title }));

    request.next({ type: 'progress', loaded: 25, total: 100 });
    expect(queue.item(id)?.state).toBe('uploading');
    expect(queue.item(id)?.progress).toBe(25);

    request.next({ type: 'processing' });
    expect(queue.item(id)?.state).toBe('processing');
    expect(queue.item(id)?.progress).toBe(100);

    request.next({ type: 'success', result: 77 });
    expect(queue.item(id)?.state).toBe('success');
    expect(queue.item(id)?.result).toBe(77);
  });

  it('retries only the failed item and keeps successes intact', () => {
    const attempts = new Map<number, Subject<UploadQueueEvent<number>>[]>();
    const queue = new UploadQueue<Payload, number>(item => {
      const request = new Subject<UploadQueueEvent<number>>();
      attempts.set(item.id, [...(attempts.get(item.id) || []), request]);
      return request;
    }, 2);
    const [first, second] = queue.add([
      { file: file('uno.jpg'), title: 'Uno' },
      { file: file('dos.jpg'), title: 'Dos' },
    ]);
    queue.start(item => ({ file: item.file, title: item.title }));
    attempts.get(first)![0].next({ type: 'success', result: 1 });
    attempts.get(second)![0].error({ status: 400, error: { message: 'archivo corrupto' } });

    expect(queue.item(first)?.state).toBe('success');
    expect(queue.item(second)?.error).toBe('archivo corrupto');

    expect(queue.retry(second)).toBe(true);
    expect(attempts.get(first)?.length).toBe(1);
    expect(attempts.get(second)?.length).toBe(2);
    expect(queue.item(first)?.state).toBe('success');
  });

  it('does not retry an ambiguous transport or server failure', () => {
    const request = new Subject<UploadQueueEvent<number>>();
    const queue = new UploadQueue<Payload, number>(() => request, 1);
    const [id] = queue.add([{ file: file('incierto.jpg'), title: 'Incierto' }]);
    queue.start(item => ({ file: item.file, title: item.title }));

    request.error({ status: 0, message: 'connection lost' });

    expect(queue.item(id)?.state).toBe('failed');
    expect(queue.item(id)?.retryable).toBe(false);
    expect(queue.item(id)?.error).toContain('Revisa el álbum');
    expect(queue.retry(id)).toBe(false);
  });

  it('keeps safe structured conflict details and can replace the frozen payload', () => {
    const attempts: Payload[] = [];
    const requests: Subject<UploadQueueEvent<number>>[] = [];
    const queue = new UploadQueue<Payload, number>(item => {
      attempts.push(item.payload!);
      const request = new Subject<UploadQueueEvent<number>>();
      requests.push(request);
      return request;
    }, 1);
    const [id] = queue.add([{ file: file('duplicada.jpg'), title: 'Duplicada' }]);
    queue.start(item => ({ file: item.file, title: item.title }));
    requests[0].error({
      status: 409,
      error: {
        code: 'exact_duplicate',
        message: 'Ya existe',
        existing_media_id: 41,
        existing_album_id: 7,
        unsafe: { nested: true },
      },
    });

    expect(queue.item(id)?.errorCode).toBe('exact_duplicate');
    expect(queue.item(id)?.errorDetails).toEqual({ existing_media_id: 41, existing_album_id: 7 });
    const replacement = { ...attempts[0], title: 'Duplicada confirmada' };
    expect(queue.retry(id, replacement)).toBe(true);
    expect(attempts[1]).toBe(replacement);
    expect(queue.item(id)?.errorCode).toBeUndefined();
    expect(queue.item(id)?.errorDetails).toBeUndefined();
  });

  it('resolves a failed duplicate without uploading again, and never touches other states', () => {
    const requests: Subject<UploadQueueEvent<number>>[] = [];
    const queue = new UploadQueue<Payload, number>(() => {
      const request = new Subject<UploadQueueEvent<number>>();
      requests.push(request);
      return request;
    }, 1);
    const [id, queued] = queue.add([{ file: file('a.jpg'), title: 'A' }, { file: file('b.jpg'), title: 'B' }]);
    queue.start(item => ({ file: item.file, title: item.title }));
    expect(queue.resolve(queued)).toBe(false);
    requests[0].error({ status: 409, error: { code: 'exact_duplicate', existing_media_id: 41, existing_album_id: null } });

    expect(queue.resolve(id)).toBe(true);
    expect(queue.item(id)).toEqual(expect.objectContaining({ state: 'success', error: '', errorCode: undefined, retryable: false }));
    expect(queue.resolve(id)).toBe(false);
    expect(requests.length).toBe(2);
  });

  it('cancels queued/uploading items, starts the next one, and never cancels processing', () => {
    const requests = new Map<number, Subject<UploadQueueEvent<number>>>();
    const cancelled = new Set<number>();
    const queue = new UploadQueue<Payload, number>(item => new Observable(subscriber => {
      const request = new Subject<UploadQueueEvent<number>>();
      requests.set(item.id, request);
      const subscription = request.subscribe(subscriber);
      return () => { cancelled.add(item.id); subscription.unsubscribe(); };
    }), 1);
    const [first, second, third] = queue.add([
      { file: file('uno.jpg'), title: 'Uno' },
      { file: file('dos.jpg'), title: 'Dos' },
      { file: file('tres.jpg'), title: 'Tres' },
    ]);
    expect(queue.cancel(third)).toBe(true);
    queue.start(item => ({ file: item.file, title: item.title }));

    expect(queue.cancel(first)).toBe(true);
    expect(cancelled.has(first)).toBe(true);
    expect(queue.item(first)?.state).toBe('cancelled');
    expect(queue.item(second)?.state).toBe('uploading');
    expect(queue.item(third)?.state).toBe('cancelled');

    requests.get(second)!.next({ type: 'processing' });
    expect(queue.cancel(second)).toBe(false);
    expect(queue.item(second)?.state).toBe('processing');
  });

  it('settles a mixed 30-file batch and retries only its failures', () => {
    const attempts = new Map<number, Subject<UploadQueueEvent<number>>[]>();
    let active = 0;
    let peak = 0;
    const queue = new UploadQueue<Payload, number>(item => new Observable(subscriber => {
      active++;
      peak = Math.max(peak, active);
      const request = new Subject<UploadQueueEvent<number>>();
      attempts.set(item.id, [...(attempts.get(item.id) || []), request]);
      const subscription = request.subscribe(subscriber);
      return () => { active--; subscription.unsubscribe(); };
    }), 3);
    const ids = queue.add(Array.from({ length: 30 }, (_, index) => ({
      file: file(`${index + 1}.jpg`), title: `${index + 1}`,
    })));
    const failures = new Set([ids[4], ids[11], ids[20]]);
    queue.cancel(ids[29]);
    queue.start(item => ({ file: item.file, title: item.title }));

    let guard = 0;
    while (queue.running() && guard++ < 100) {
      const current = queue.items().find(item => item.state === 'uploading')!;
      const request = attempts.get(current.id)!.at(-1)!;
      if (failures.has(current.id) && attempts.get(current.id)!.length === 1) {
        request.error({ status: 400, error: { message: 'fallo controlado' } });
      } else {
        request.next({ type: 'success', result: current.id });
      }
    }

    expect(guard).toBeLessThan(100);
    expect(peak).toBe(3);
    expect(queue.successCount()).toBe(26);
    expect(queue.items().filter(item => item.state === 'failed').length).toBe(3);
    expect(queue.item(ids[29])?.state).toBe('cancelled');

    failures.forEach(id => expect(queue.retry(id)).toBe(true));
    guard = 0;
    while (queue.running() && guard++ < 20) {
      const current = queue.items().find(item => item.state === 'uploading')!;
      attempts.get(current.id)!.at(-1)!.next({ type: 'success', result: current.id });
    }

    expect(queue.successCount()).toBe(29);
    expect(queue.items().filter(item => item.state === 'failed').length).toBe(0);
    expect(peak).toBe(3);
  });
});
