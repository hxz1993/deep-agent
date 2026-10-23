// Share imports and initialization; navigation decides which loaded page is visible.
export function createPageLoader(loaders) {
  const pending = new Map();
  return function load(name) {
    if (!pending.has(name)) {
      const promise = Promise.resolve().then(() => loaders[name]()).catch(error => {
        if (pending.get(name) === promise) pending.delete(name);
        throw error;
      });
      pending.set(name, promise);
    }
    return pending.get(name);
  };
}
