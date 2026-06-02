/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#101216",
        panel: "#181b20",
        line: "#2a2f36",
      },
    },
  },
  plugins: [],
};
