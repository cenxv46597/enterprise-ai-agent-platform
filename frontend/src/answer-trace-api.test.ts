import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AnswerUnconfirmedError, ApiError, answerKnowledgeBase } from './api';

const WORKSPACE_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const KNOWLEDGE_BASE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const DOCUMENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const OTHER_DOCUMENT_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
const TRACE_A = '12345678-1234-4abc-8def-1234567890ab';
const TRACE_B = '87654321-4321-4fed-9cba-ba0987654321';
const TRACE_C = 'abcdef12-abcd-4123-abcd-abcdef123456';
const PRIVATE = 'PRIVATE-RAW-ERROR-DETAIL';
const DOCUMENT = {
  id: DOCUMENT_ID, knowledge_base_id: KNOWLEDGE_BASE_ID, original_filename: 'demo.md', status: 'ready',
};
const CITATION = {
  chunk_id: 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee', document_id: DOCUMENT_ID,
  document_name: 'demo.md', chunk_index: 1, quote: '完整原文', start_char: 0, end_char: 4,
};
const ANSWER = { answer: '根据资料给出的回答。', abstained: false, citations: [CITATION], reranking: null };
const fetchMock = vi.fn<typeof fetch>();

function response(payload: unknown, status = 200, trace?: string): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json', ...(trace === undefined ? {} : { 'X-Trace-Id': trace }) },
  });
}

const ask = (signal = new AbortController().signal, question = '问题') =>
  answerKnowledgeBase(WORKSPACE_ID, KNOWLEDGE_BASE_ID, question, signal);

async function rejected(action: Promise<unknown>): Promise<ApiError> {
  const failure: unknown = await action.then(
    () => { throw new Error('Expected a rejected answer request'); },
    (error: unknown) => error,
  );
  expect(failure).toBeInstanceOf(ApiError);
  expect(String(failure)).not.toContain(PRIVATE);
  return failure as ApiError;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('answer HTTP failure trace provenance', () => {
  it('normalizes complete UUID4 trace values in both public error constructors', () => {
    expect(new ApiError('safe', 409, TRACE_A.toUpperCase())).toMatchObject({ status: 409, traceId: TRACE_A });
    expect(new AnswerUnconfirmedError(502, TRACE_B.toUpperCase())).toMatchObject({
      status: 502, traceId: TRACE_B,
    });
    expect(new ApiError('safe').traceId).toBeNull();
    expect(new AnswerUnconfirmedError().traceId).toBeNull();
  });

  it.each([
    null, undefined, 123, {}, '', 'not-a-uuid',
    `${TRACE_A}\n`, ` ${TRACE_A}`, `${TRACE_A} `, `${TRACE_A}, ${TRACE_B}`,
    '12345678-1234-1abc-8def-1234567890ab',
    '12345678-1234-4abc-7def-1234567890ab',
    '00000000-0000-0000-0000-000000000000',
  ])('ignores untrusted constructor trace value %j', (value) => {
    expect(new ApiError('safe', 409, value).traceId).toBeNull();
    expect(new AnswerUnconfirmedError(502, value).traceId).toBeNull();
  });

  it.each([409, 502, 201])('retains only the POST HTTP %s response header without parsing its error body or retrying', async (status) => {
    const failed = response({ detail: PRIVATE, trace_id: TRACE_B }, status, TRACE_A.toUpperCase());
    const read = vi.spyOn(failed, 'json');
    fetchMock.mockResolvedValueOnce(failed);
    const error = await rejected(ask());
    expect(error).toMatchObject({ status, traceId: TRACE_A });
    expect(error instanceof AnswerUnconfirmedError).toBe(status !== 409);
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.has('X-Trace-Id')).toBe(false);
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.has('Cookie')).toBe(false);
  });

  it.each([undefined, 'bad-trace', `${TRACE_A}, ${TRACE_B}`, '12345678-1234-1abc-8def-1234567890ab'])(
    'does not trust an error JSON trace when the POST header is missing or invalid (%s)', async (trace) => {
      fetchMock.mockResolvedValueOnce(response({ detail: PRIVATE, trace_id: TRACE_A, traceId: TRACE_B }, 502, trace));
      expect(await rejected(ask())).toMatchObject({ status: 502, traceId: null });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it('rejects multiple X-Trace-Id header values rather than selecting one', async () => {
    const headers = new Headers({ 'Content-Type': 'application/json' });
    headers.append('X-Trace-Id', TRACE_A);
    headers.append('X-Trace-Id', TRACE_B);
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: PRIVATE }), { status: 409, headers }));
    expect(await rejected(ask())).toMatchObject({ status: 409, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([401, 404, 502])('uses the failing source GET HTTP %s trace instead of the successful POST trace', async (status) => {
    const failed = response({ detail: PRIVATE, trace_id: TRACE_C }, status, TRACE_B);
    const read = vi.spyOn(failed, 'json');
    fetchMock.mockResolvedValueOnce(response(ANSWER, 200, TRACE_A)).mockResolvedValueOnce(failed);
    const error = await rejected(ask());
    expect(error).toMatchObject({ status, traceId: TRACE_B });
    expect(error instanceof AnswerUnconfirmedError).toBe(status !== 401);
    expect(read).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [, options] of fetchMock.mock.calls) {
      expect(new Headers(options?.headers).has('X-Trace-Id')).toBe(false);
    }
  });

  it.each([undefined, `${TRACE_B}, ${TRACE_C}`])('does not fall back to the POST trace when the failed source has no valid header (%s)', async (trace) => {
    fetchMock.mockResolvedValueOnce(response(ANSWER, 200, TRACE_A))
      .mockResolvedValueOnce(response({ detail: PRIVATE, trace_id: TRACE_B }, 404, trace));
    expect(await rejected(ask())).toMatchObject({ status: 404, traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([TRACE_C, undefined])('uses only the later failed source trace, including absence (%s)', async (trace) => {
    fetchMock.mockResolvedValueOnce(response({ ...ANSWER, citations: [CITATION, {
      ...CITATION, chunk_id: 'ffffffff-ffff-ffff-ffff-ffffffffffff', document_id: OTHER_DOCUMENT_ID,
    }] }, 200, TRACE_A)).mockResolvedValueOnce(response(DOCUMENT, 200, TRACE_B))
      .mockResolvedValueOnce(response({ detail: PRIVATE }, 404, trace));
    expect(await rejected(ask())).toMatchObject({ status: 404, traceId: trace ?? null });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each([
    { ...DOCUMENT, id: OTHER_DOCUMENT_ID },
    { ...DOCUMENT, knowledge_base_id: OTHER_DOCUMENT_ID },
    { ...DOCUMENT, original_filename: 'different.md' },
    { ...DOCUMENT, status: 'parsed' },
  ])('keeps successful-HTTP source validation failures unnumbered: %j', async (document) => {
    fetchMock.mockResolvedValueOnce(response(ANSWER, 200, TRACE_A))
      .mockResolvedValueOnce(response(document, 200, TRACE_B));
    expect(await rejected(ask())).toMatchObject({ name: 'AnswerUnconfirmedError', traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('uses the source HTTP 201 trace when its status violates the expected 200 contract', async () => {
    fetchMock.mockResolvedValueOnce(response(ANSWER, 200, TRACE_A))
      .mockResolvedValueOnce(response(DOCUMENT, 201, TRACE_B));
    expect(await rejected(ask())).toMatchObject({ name: 'AnswerUnconfirmedError', status: null, traceId: TRACE_B });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not assign a successful POST trace to an invalid answer body', async () => {
    fetchMock.mockResolvedValueOnce(response({ ...ANSWER, citations: [] }, 200, TRACE_A));
    expect(await rejected(ask())).toMatchObject({ name: 'AnswerUnconfirmedError', traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not assign a successful POST trace to invalid JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response(PRIVATE, { status: 200, headers: { 'X-Trace-Id': TRACE_A } }));
    expect(await rejected(ask())).toMatchObject({ name: 'AnswerUnconfirmedError', traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not assign either successful response trace to invalid source JSON', async () => {
    fetchMock.mockResolvedValueOnce(response(ANSWER, 200, TRACE_A))
      .mockResolvedValueOnce(new Response(PRIVATE, { status: 200, headers: { 'X-Trace-Id': TRACE_B } }));
    expect(await rejected(ask())).toMatchObject({ name: 'AnswerUnconfirmedError', traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(['POST', 'source GET'])('leaves %s network failures unnumbered and never retries', async (phase) => {
    if (phase === 'source GET') fetchMock.mockResolvedValueOnce(response(ANSWER, 200, TRACE_A));
    fetchMock.mockRejectedValueOnce(new Error(PRIVATE));
    expect(await rejected(ask())).toMatchObject({ name: 'AnswerUnconfirmedError', traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(phase === 'POST' ? 1 : 2);
  });

  it('leaves local validation and pre-cancellation unnumbered without sending HTTP', async () => {
    expect(await rejected(ask(undefined, '  '))).toMatchObject({ traceId: null });
    const parent = new AbortController();
    parent.abort();
    expect(await rejected(ask(parent.signal))).toMatchObject({ name: 'AnswerUnconfirmedError', traceId: null });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['cancellation', 'timeout'])('ignores a late numbered HTTP failure after POST %s', async (interruption) => {
    vi.useFakeTimers();
    let finish!: (value: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const parent = new AbortController();
    const action = rejected(ask(parent.signal));
    if (interruption === 'cancellation') parent.abort();
    else await vi.advanceTimersByTimeAsync(630_000);
    finish(response({ detail: PRIVATE }, 502, TRACE_A));
    expect(await action).toMatchObject({ name: 'AnswerUnconfirmedError', traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(['cancellation', 'timeout'])('ignores a late numbered source failure after %s', async (interruption) => {
    vi.useFakeTimers();
    let finish!: (value: Response) => void;
    fetchMock.mockResolvedValueOnce(response(ANSWER, 200, TRACE_A))
      .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const parent = new AbortController();
    const action = rejected(ask(parent.signal));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    if (interruption === 'cancellation') parent.abort();
    else await vi.advanceTimersByTimeAsync(10_000);
    finish(response({ detail: PRIVATE }, 404, TRACE_B));
    expect(await action).toMatchObject({ name: 'AnswerUnconfirmedError', traceId: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });
});
