const colors = [
  "surface",
  "surface-raised",
  "surface-sunken",
  "border",
  "border-strong",
  "text",
  "text-muted",
  "text-subtle",
  "text-on-accent",
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
  safelist: [
    { pattern: new RegExp(`^(bg|text|border|ring)-(${tokenName})$`) },
  ],
  plugins: [],
}
