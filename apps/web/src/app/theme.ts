import type { ThemeConfig } from 'antd';

export const theme: ThemeConfig = {
  token: {
    colorPrimary: '#2463F5',
    colorInfo: '#2463F5',
    colorSuccess: '#08a56b',
    colorWarning: '#d68b12',
    colorError: '#db3f52',
    colorText: '#142448',
    colorTextSecondary: '#71809f',
    colorBgLayout: '#f2f6fe',
    colorBorder: '#dce5f4',
    borderRadius: 9,
    controlHeight: 40,
    fontSize: 14,
    fontFamily: 'Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  components: {
    Button: { primaryShadow: '0 3px 8px rgba(36,99,245,.16)', fontWeight: 600 },
    Table: {
      headerBg: '#f0f5fd',
      headerColor: '#637394',
      borderColor: '#edf1f8',
      cellPaddingBlock: 12,
    },
    Drawer: { paddingLG: 24 },
    Segmented: { trackBg: '#eef4ff', itemSelectedColor: '#2463f5' },
    Select: { optionSelectedBg: '#eaf1ff' },
  },
};
