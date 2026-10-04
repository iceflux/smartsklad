const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const template = fs.readFileSync(process.argv[2], 'utf8');
const blocks = [...template.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
/* Блок выбирается по содержимому, а не «последним в файле»: в base.html их
   теперь несколько, и живая фильтрация добавила свой. */
const block = blocks.find(b => /data-menu-toggle/.test(b));
assert.ok(block, 'base.html renders no bare <script> for the sidebar');
assert.ok(!/onclick\s*=/.test(template), 'the markup carries an inline handler');
assert.ok(/data-menu-toggle/.test(template), 'no button carries data-menu-toggle');
assert.ok(/data-menu-overlay/.test(template), 'the markup renders no overlay hook');

function run() {
    const listeners = {};
    const body = {
        _v: {'data-nav': 'closed'},
        getAttribute(k) { return this._v[k] === undefined ? null : this._v[k]; },
        setAttribute(k, v) { this._v[k] = v; },
    };
    const toggle = {
        _v: {'aria-expanded': 'false'},
        addEventListener: (e, fn) => { listeners['toggle:' + e] = fn; },
        setAttribute(k, v) { this._v[k] = v; },
    };
    const overlay = {addEventListener: (e, fn) => { listeners['overlay:' + e] = fn; }};
    const sandbox = {
        document: {
            body,
            querySelector: (sel) => (sel === '[data-menu-toggle]' ? toggle
                : sel === '[data-menu-overlay]' ? overlay : null),
            addEventListener: (e, fn) => { listeners['doc:' + e] = fn; },
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(block, sandbox);
    return {body, listeners, toggle};
}

const opened = run();
assert.strictEqual(opened.body.getAttribute('data-nav'), 'closed', 'the server sent a closed menu');
opened.listeners['toggle:click']();
assert.strictEqual(opened.body.getAttribute('data-nav'), 'open', 'the button must open the menu');
opened.listeners['overlay:click']();
assert.strictEqual(opened.body.getAttribute('data-nav'), 'closed', 'the overlay must close the menu');
opened.listeners['toggle:click']();
opened.listeners['toggle:click']();
assert.strictEqual(opened.body.getAttribute('data-nav'), 'closed', 'a second click must close it again');
opened.listeners['doc:keydown']({key: 'Escape'});
assert.strictEqual(opened.body.getAttribute('data-nav'), 'closed');

const fresh = run();
fresh.listeners['toggle:click']();
fresh.listeners['doc:keydown']({key: 'Escape'});
assert.strictEqual(fresh.body.getAttribute('data-nav'), 'closed', 'Escape must close an open menu');

const aria = run();
assert.strictEqual(aria.toggle._v['aria-expanded'], 'false', 'the markup seeds the button closed');
aria.listeners['toggle:click']();
assert.strictEqual(aria.toggle._v['aria-expanded'], 'true', 'opening must set aria-expanded');
aria.listeners['overlay:click']();
assert.strictEqual(aria.toggle._v['aria-expanded'], 'false', 'the overlay must reset aria-expanded');
aria.listeners['toggle:click']();
aria.listeners['doc:keydown']({key: 'Escape'});
assert.strictEqual(aria.toggle._v['aria-expanded'], 'false', 'Escape must reset aria-expanded');

assert.ok(/aria-expanded="false"/.test(template), 'the menu button ships no initial aria-expanded');

console.log('ok');