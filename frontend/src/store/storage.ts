/**
 * Copyright © 2026 广州金元信息科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 */

import { ProxyPersistStorageEngine } from './valtio-persist'

const storage: ProxyPersistStorageEngine = {
  getItem: (name) => window.localStorage.getItem(name),
  setItem: (name, value) => window.localStorage.setItem(name, value),
  removeItem: (name) => window.localStorage.removeItem(name),
  getAllKeys: () => Object.keys(window.localStorage),
}

export default storage
