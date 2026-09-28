const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const template = fs.readFileSync(process.argv[2], 'utf8');
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const blocks = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.ok(blocks.length > 0, 'no plain <script> block in the template');
const block = blocks[blocks.length - 1];
assert.ok(!/onclick\s*=/.test(template), 'the markup carries an inline handler');

function run(options) {
    const calls = {deletes: [], alerts: [], prompts: [], confirms: []};
    const button = {
        dataset: {id: String(options.id)},
        addEventListener: (event, fn) => { button.click = fn; },
    };
    const sandbox = {
        document: {
            getElementById: () => ({textContent: JSON.stringify(options.alone ? cases.tree_alone : cases.tree)}),
            querySelectorAll: () => [button],
        },
        window: {location: {}},
        confirm: () => { calls.confirms.push(true); return options.confirm !== false; },
        prompt: (message) => { calls.prompts.push(message); return options.answer; },
        alert: (message) => { calls.alerts.push(message); },
        fetch: (url, opts) => {
            if (opts && opts.method === 'DELETE') {
                calls.deletes.push(url);
                return Promise.resolve({ok: true});
            }
            if (options.contentsStatus === 404) {
                return Promise.resolve({ok: false, json: () => Promise.resolve({detail: 'нет'})});
            }
            return Promise.resolve({ok: true, json: () => Promise.resolve(options.contents)});
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(block, sandbox);
    button.click();
    return new Promise(resolve => setTimeout(() => resolve({calls, sandbox}), 0));
}

(async () => {
    const empty = await run({id: 1, contents: {products: 0, children: 0}, answer: null});
    assert.deepStrictEqual(empty.calls.deletes, ['/api/categories/1?resolve=true']);
    assert.deepStrictEqual(empty.calls.prompts, [], 'an empty category must not prompt');

    const full = {products: 2, children: 1};
    const moved = await run({id: 1, contents: full, answer: '1'});
    assert.deepStrictEqual(moved.calls.deletes, ['/api/categories/1?resolve=true&reassign_to=3']);
    assert.ok(moved.calls.prompts[0].includes('2 товара и 1 подкатегория'), moved.calls.prompts[0]);
    assert.ok(moved.calls.prompts[0].includes('1 — Другое'), moved.calls.prompts[0]);
    assert.ok(moved.calls.prompts[0].includes('2 — Лист'), moved.calls.prompts[0]);
    assert.ok(!moved.calls.prompts[0].includes('Ветка'), 'a descendant must not be offered');

    const second = await run({id: 1, contents: full, answer: '2'});
    assert.deepStrictEqual(second.calls.deletes, ['/api/categories/1?resolve=true&reassign_to=4']);

    const child = await run({id: 2, contents: full, answer: '1'});
    assert.deepStrictEqual(child.calls.deletes, ['/api/categories/2?resolve=true&reassign_to=1']);
    assert.ok(!child.calls.prompts[0].includes('2 — Ветка'), 'the deleted category must not be offered');

    const cleared = await run({id: 1, contents: full, answer: '   '});
    assert.deepStrictEqual(cleared.calls.deletes, ['/api/categories/1?resolve=true']);

    const cancelled = await run({id: 1, contents: full, answer: null});
    assert.deepStrictEqual(cancelled.calls.deletes, [], 'cancel must not delete');

    const garbage = await run({id: 1, contents: full, answer: 'abc'});
    assert.deepStrictEqual(garbage.calls.deletes, []);
    assert.strictEqual(garbage.calls.alerts.length, 1);

    const alone = await run({id: 9, contents: full, answer: null, alone: true});
    assert.deepStrictEqual(alone.calls.deletes, ['/api/categories/9?resolve=true']);
    assert.deepStrictEqual(alone.calls.prompts, [], 'no other category means no number to type');
    assert.deepStrictEqual(alone.calls.confirms, [true]);

    const refused = await run({id: 9, contents: full, answer: null, alone: true, confirm: false});
    assert.deepStrictEqual(refused.calls.deletes, []);

    const unreadable = await run({id: 1, contents: full, answer: '1', contentsStatus: 404});
    assert.deepStrictEqual(unreadable.calls.deletes, [], 'a failed read must not delete');
    assert.deepStrictEqual(unreadable.calls.prompts, []);
    assert.strictEqual(unreadable.calls.alerts.length, 1);

    const plural = vm.runInContext('plural', moved.sandbox);
    for (const [n, word] of cases.plural) {
        assert.strictEqual(plural(n, 'товар', 'товара', 'товаров'), word, 'n=' + n);
    }
    console.log('ok');
})().catch((e) => {
    console.error(e.message);
    process.exit(1);
});
