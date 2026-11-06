// Console messages some engines log for valid pages. Linux WebKit doesn't know the viewport's
// interactive-widget key, which Chrome on phones uses for the on-screen keyboard.
const benign=[/^Viewport argument key "interactive-widget" not recognized and ignored\.$/];
export const benignConsole=text=>benign.some(pattern=>pattern.test(text));
