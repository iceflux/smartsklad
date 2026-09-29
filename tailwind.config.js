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

module.exports = {
  darkMode: "class",
  content: [
    "./app/templates/**/*.html",
  ],
  theme: { extend: { colors: tokenColors } },
  plugins: [],
}
