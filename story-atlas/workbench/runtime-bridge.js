/* Served after the original qSpider module, only in this local explorer.
 * Private bindings are version-guarded by server.py; source bundle stays untouched.
 */
const atlasMemory = new Map();
getStorage = () => ({
  getSavedSlots: async () => [], getNamedSaves: async () => [],
  saveBySlot: async (game,slot,bytes) => atlasMemory.set(game+':slot:'+slot,bytes),
  saveByKey: async (game,key,bytes) => atlasMemory.set(game+':key:'+key,bytes),
  getSaveDataBySlot: async (game,slot) => atlasMemory.get(game+':slot:'+slot)||null,
  getSaveDataByKey: async (game,key) => atlasMemory.get(game+':key:'+key)||null,
  clearSaveSlot: async (game,slot) => atlasMemory.delete(game+':slot:'+slot),
  clearSaveKey: async (game,key) => atlasMemory.delete(game+':key:'+key)
});
window.girllifeCloudCredentials = async () => null;
const atlasPause = () => { isPaused$.set(true); clearTimeout(counterTimeout$.value); };
const atlasPending = () => input$.value.isOpen || msg$.value.isOpen || menu$.value.isOpen;
window.atlasEngine = {
  ready: () => !!qspApi$.value && !!currentGameEntry$.value && baseInit$.value,
  read() {
    view$.actions.close();
    const input = input$.value, message = msg$.value, menu = menu$.value;
    return {
      html: nextMainContent$.value || mainContent$.value,
      actions: actions$.value.map((action,index)=>({index,html:action.name})),
      input: input.isOpen ? {html:input.content} : null,
      message: message.isOpen ? {html:message.content} : null,
      menu: menu.isOpen ? menu.items.map((item,index)=>({index,html:item.name})) : null
    };
  },
  snapshot() { if(atlasPending())return null;atlasPause();return qspApi$.value.saveGame(); },
  restore(bytes) {
    if(atlasPending())throw new Error('請先完成目前的輸入或確認，再返回其他分支。');
    atlasPause();qspApi$.value.loadSave(bytes);onRestore();
  },
  pause: atlasPause,
  act(index) { if(!canSelectAction(index))throw new Error('這個選項已變更，請重試。');selectAction(index);execSelectedAction(); },
  link(href) { if(!href.toLowerCase().startsWith('exec:'))throw new Error('這個連結不是遊戲選項。');onLinkClicked(href); },
  answer(value) { input$.actions.enter(value);input$.actions.finish(); },
  confirm() { msg$.actions.close(); },
  choose(index) { menu$.actions.select(index); }
};
// Crawler-only checkpoint includes its isolated virtual save files too.
window.atlasEngine.checkpoint = () => {
  const save = window.atlasEngine.snapshot();
  return save ? {save, files:[...atlasMemory.entries()]} : null;
};
window.atlasEngine.restoreCheckpoint = async checkpoint => {
  // Cancel only the crawler's own pending modal, then replace all game state.
  for(let attempt=0;attempt<20&&atlasPending();attempt++) {
    if(input$.value.isOpen)input$.actions.close();
    else if(msg$.value.isOpen)msg$.actions.close();
    else if(menu$.value.isOpen)menu$.actions.close();
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  if(atlasPending())throw new Error('目前的遊戲對話無法結束，已停止這條分支。');
  atlasMemory.clear();for(const [key,bytes]of checkpoint.files)atlasMemory.set(key,bytes);
  window.atlasEngine.restore(checkpoint.save);
};
