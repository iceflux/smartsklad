const colors = [
  "surface",
  "surface-raised",
  "surface-sunken",
  "line",
  "line-strong",
  "primary",
  "secondary",
  "caption",
  "muted",
  "subtle",
  "on-accent",
  "accent",
  "danger",
  "danger-bg",
  "danger-border",
  "success",
  "success-bg",
  "success-border",
  "warning",
  "warning-bg",
  "warning-border",
  "warning-strong-bg",
  "warning-strong-border",
]

const tokenColors = Object.fromEntries(
  colors.map((name) => [name, `rgb(var(--color-${name}) / <alpha-value>)`]),
)

const tokenName = colors.join("|")

module.exports = {
  darkMode: "class",
  content: [
    "./app/templates/**/*.html",
  ],
  theme: { extend: { colors: tokenColors } },
  // ВРЕМЕННО, до замены литералов в шаблонах: ни один шаблон токенами пока не
  // пользуется, без safelist сборка не выдаст ни одного из этих классов.
  // Убрать вместе с последней заменой в Task 3 — примерно 7.9 КБ мёртвого CSS.
  safelist: [
    { pattern: new RegExp(`^(bg|text|border|ring)-(${tokenName})$`) },
  ],
  plugins: [],
}
