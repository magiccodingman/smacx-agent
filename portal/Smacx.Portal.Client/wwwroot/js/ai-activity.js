// Request and call identities reconcile transport events; repeated prompts are never read.
export function applyEvent(state, event) {
    if (state.seen.has(event.event_id)) return;
    state.seen.add(event.event_id);
    const p = event.payload || {}, c = event.correlation || {};
    const key = `${c.run_id || ''}:${c.request_id || event.stream_id}`;
    let item = state.items.get(key);
    if (!item) {
        item = { key, run: c.run_id, time: event.recorded_unix, text: '', thinking: '', tools: new Map(), streamCalls: new Map(), status: 'Waiting for provider' };
        state.items.set(key, item);
    }
    if (p.type === 'response_started') { item.model = p.model; item.effort = p.reasoning_effort; }
    if (p.type === 'response_delta' || p.type === 'response_body') {
        const chunks = p.type === 'response_body' ? [p.body] : p.chunks || [];
        for (const chunk of chunks) for (const choice of chunk.choices || []) {
            if ((choice.index || 0) !== 0) continue;
            const d = choice.delta || choice.message || {};
            item.text += typeof d.content === 'string' ? d.content : '';
            item.thinking += typeof d.reasoning_content === 'string' ? d.reasoning_content : typeof d.reasoning === 'string' ? d.reasoning : '';
            for (const emitted of d.tool_calls || []) {
                const index = emitted.index || 0;
                const previous = item.streamCalls.get(index);
                const id = emitted.id || previous || `pending-${index}`;
                const tool = item.tools.get(previous || id) || { id, name:'', argumentText:'', status:'Preparing' };
                if (previous && previous !== id) item.tools.delete(previous);
                tool.id = id; tool.name += emitted.function?.name || '';
                tool.argumentText += emitted.function?.arguments || '';
                tool.arguments = tool.argumentText;
                item.streamCalls.set(index,id); item.tools.set(id,tool);
            }
            item.status = 'Receiving output';
        }
        if (p.type === 'response_body') item.status = 'Response received';
    }
    if (p.type === 'response_finished') item.status = p.truncated ? 'Diagnostic capture incomplete' : p.complete ? 'Response received' : 'Response interrupted';
    if (p.type === 'provider_transport_failed') item.status = 'Provider request failed';
    if (p.type === 'tool_requested' || p.type === 'tool_returned' || p.type === 'tool_validation_rejected') {
        const id = c.call_id || event.event_id;
        const tool = item.tools.get(id) || { id };
        tool.name = p.managed_name || 'Tool';
        if (p.type === 'tool_requested') { tool.arguments = p.arguments; tool.status = 'Running'; }
        else {
            tool.result = p.content ?? p.error; tool.status = 'Returned';
            let result = tool.result;
            if (typeof result === 'string') { try { result = JSON.parse(result); } catch { } }
            tool.result = result;
            if (p.type === 'tool_validation_rejected' || result?.ok === false || result?.isError === true) tool.status = 'Returned · error';
            if (p.type === 'tool_validation_rejected') tool.arguments = p.arguments;
        }
        item.tools.set(id, tool);
    }
    if (p.type === 'tool_batch_finished' && (p.exception_type || p.missing_result_call_ids?.length))
        item.status = 'Tool execution interrupted';
    if (p.type === 'capture_gap' || p.capture_status === 'omitted') item.status = 'Capture incomplete';
}

export function create(root, match, seat) {
    const state = { items: new Map(), seen: new Set() }, cards = new Map();
    const feed = root.querySelector('.activity-feed'), status = root.querySelector('.activity-status');
    const newer = root.querySelector('.activity-new');
    let cursor = '', disposed = false, following = true, timer;
    const controller = new AbortController();
    const endpoint = s => `/api/lobbies/${encodeURIComponent(match)}/ai-activity/${s}`;
    function node(tag, cls, text) {
        const el = document.createElement(tag); if (cls) el.className = cls;
        if (text !== undefined) el.textContent = text; return el;
    }
    function bottom() { following = true; feed.scrollTop = feed.scrollHeight; newer.hidden = true; }
    const onScroll = () => { following = feed.scrollHeight - feed.clientHeight - feed.scrollTop < 48; if (following) newer.hidden = true; };
    feed.addEventListener('scroll', onScroll); newer.addEventListener('click', bottom);
    function render() {
        for (const [key, item] of [...state.items].sort((a,b) => a[1].time-b[1].time)) {
            let card = cards.get(key);
            if (!card) {
                const el = node('article', 'activity-message');
                const heading = node('div', 'activity-message-heading');
                const thinking = node('details', 'activity-thinking');
                thinking.append(node('summary', '', 'Thinking')); const thought = node('pre'); thinking.append(thought);
                const text = node('div', 'activity-answer'); const tools = node('div', 'activity-tools');
                const phase = node('div', 'activity-phase');
                el.append(heading, thinking, text, tools, phase);
                const next = [...cards.keys()].find(k => state.items.get(k)?.time > item.time);
                feed.insertBefore(el, next ? cards.get(next).el : null);
                card = { el, heading, thinking, thought, text, tools, phase, calls: new Map() }; cards.set(key, card);
            }
            card.heading.textContent = `${item.model || 'AI player'} · ${new Date(item.time * 1000).toLocaleTimeString()}${item.effort ? ' · ' + item.effort : ''}`;
            card.thought.textContent = item.thinking || 'No reasoning content emitted yet.';
            card.text.textContent = item.text;
            card.phase.textContent = item.status;
            for (const [id, call] of card.calls) if (!item.tools.has(id)) { call.title.parentElement.remove(); card.calls.delete(id); }
            for (const [id, tool] of item.tools) {
                let call = card.calls.get(id);
                if (!call) {
                    const el = node('details'); const title = node('summary'); const content = node('pre');
                    el.append(title, content); card.tools.append(el); call = { title, content }; card.calls.set(id, call);
                }
                call.title.textContent = `${tool.name} · ${tool.status}`;
                call.content.textContent = JSON.stringify({ arguments: tool.arguments, result: tool.result }, null, 2);
            }
        }
        // Keep the live DOM bounded; JSON export reads retained history independently.
        while (state.items.size > 200) {
            const key = [...state.items].sort((a,b) => a[1].time-b[1].time)[0][0];
            cards.get(key)?.el.remove(); cards.delete(key); state.items.delete(key); state.trimmed = true;
        }
        if (state.seen.size > 20000) state.seen = new Set([...state.seen].slice(-10000));
        if (following) bottom(); else newer.hidden = false;
    }
    async function page(s, position) {
        const response = await fetch(`${endpoint(s)}?cursor=${encodeURIComponent(position)}`, { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error(response.status === 403 ? 'Transcript access is no longer available.' : 'Activity connection interrupted. Reconnecting…');
        const result = await response.json(); if (!result.ok) throw new Error(result.error?.message || 'Activity unavailable'); return result.data;
    }
    async function poll() {
        if (disposed) return;
        if (root.hidden) { timer = setTimeout(poll, 500); return; }
        let delay = 3000;
        try {
            const data = await page(seat, cursor); cursor = data.cursor;
            for (const e of data.events) applyEvent(state, e);
            if (data.events.length) render();
            status.textContent = data.gaps.length ? `Capture gap: ${data.gaps.join(', ')}` :
                data.status === 'sleeping' ? 'Sleeping · wakes for its turn or a message' :
                data.status === 'communicating' ? 'Live · processing communication' :
                state.items.size ? (data.status === 'running' ? 'Live · following this AI seat' : 'AI stopped · retained activity') :
                'No activity captured yet. Older runs may only have the diagnostic ZIP.';
            if (state.trimmed) status.textContent += ' · Latest 200 replies; download for older history.';
            if (data.has_more) delay = 100;
            else if (data.status !== 'running') delay = 10000;
        } catch (e) { if (!disposed) status.textContent = e.message; }
        if (!disposed) timer = setTimeout(poll, delay);
    }
    poll();
    return { dispose() { disposed = true; controller.abort(); clearTimeout(timer); feed.removeEventListener('scroll', onScroll); newer.removeEventListener('click', bottom); } };
}

export async function toggleFullscreen(shell) {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await shell.requestFullscreen();
}
export async function leaveFullscreen() { if (document.fullscreenElement) await document.exitFullscreen(); }
