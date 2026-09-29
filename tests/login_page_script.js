const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const template = fs.readFileSync(process.argv[2], 'utf8');
const formId = process.argv[3];
const errorId = process.argv[4];
const fallback = process.argv[5];
const successRedirect = process.argv[6];
const cases = JSON.parse(fs.readFileSync(process.argv[7], 'utf8'));
const blocks = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.strictEqual(blocks.length, 1, 'expected exactly one bare <script> block, got ' + blocks.length);
const block = blocks[0];

function run(response) {
    const state = {shown: false, text: null, redirected: null, requests: []};
    const form = {
        username: {value: 'boss'},
        password: {value: 'password123'},
        token: {value: 'tok'},
        new_password: {value: 'longenough1'},
        addEventListener: (event, fn) => { form.submit = fn; },
    };
    const error = {
        set textContent(value) { state.text = value; },
        get textContent() { return state.text; },
        classList: {remove: (name) => { if (name === 'hidden') { state.shown = true; } }},
    };
    const sandbox = {
        document: {
            getElementById: (id) => {
                if (id === formId) { return form; }
                if (id === errorId) { return error; }
                throw new Error('the script looks up an unknown element ' + id);
            },
        },
        window: {location: {set href(value) { state.redirected = value; }}},
        fetch: (url, opts) => { state.requests.push([url, opts.method]); return Promise.resolve(response); },
    };
    vm.createContext(sandbox);
    vm.runInContext(block, sandbox);
    form.submit({target: form, preventDefault() {}});
    return new Promise(resolve => setTimeout(() => resolve(state), 0));
}

function jsonResponse(status, body) {
    return {ok: false, status: status, json: () => Promise.resolve(body)};
}

function unreadableResponse(status) {
    return {ok: false, status: status, json: () => Promise.reject(new SyntaxError('Unexpected token < in JSON'))};
}

(async () => {
    const throttled = await run(jsonResponse(429, {detail: cases.throttled}));
    assert.strictEqual(throttled.shown, true, 'a throttled call must show the error');
    assert.strictEqual(throttled.text, cases.throttled, 'the server text must reach the page verbatim');
    assert.strictEqual(throttled.redirected, null, 'a throttled call must not redirect');

    const refused = await run(jsonResponse(cases.refusedStatus, {detail: cases.refused}));
    assert.strictEqual(refused.text, cases.refused, 'a refused call must show the server text');
    assert.strictEqual(refused.shown, true, 'a refused call must show the error');

    const crashed = await run(unreadableResponse(500));
    assert.strictEqual(crashed.shown, true, 'a 500 must still show the error');
    assert.strictEqual(crashed.text, fallback, 'a 500 with a non-JSON body must fall back to useful text');

    const empty = await run(unreadableResponse(502));
    assert.strictEqual(empty.text, fallback, 'an unreadable body must fall back to useful text');

    const logged = await run({ok: true, status: 204, json: () => Promise.resolve({})});
    assert.strictEqual(logged.redirected, successRedirect, 'a successful call must still redirect');
    assert.strictEqual(logged.shown, false, 'a successful call must not show an error');

    console.log('ok');
})().catch((e) => {
    console.error(e.message);
    process.exit(1);
});
