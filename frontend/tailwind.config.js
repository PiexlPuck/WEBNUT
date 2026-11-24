/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          950: "#090d16",
          900: "#0f1624",
          800: "#172033",
          700: "#222e47"
        }
      }
    }
  },
  plugins: []
};
