// SPDX-License-Identifier: GPL-2.0+
/*
 * Copyright (C) 2022 StarFive Technology Co., Ltd.
 * Author: Yanhong Wang<yanhong.wang@starfivetech.com>
 */

#include <common.h>
#include <asm/arch/eeprom.h>
#include <asm/arch/regs.h>
#include <asm/arch/spl.h>
#include <asm/io.h>
#include <dt-bindings/clock/starfive,jh7110-crg.h>
#include <fdt_support.h>
#include <linux/libfdt.h>
#include <log.h>
#include <spl.h>

DECLARE_GLOBAL_DATA_PTR;
#define JH7110_CLK_CPU_ROOT_OFFSET		0x0U
#define JH7110_CLK_CPU_ROOT_SHIFT		24
#define JH7110_CLK_CPU_ROOT_MASK		GENMASK(29, 24)

struct starfive_vf2_pro {
	const char *path;
	const char *name;
	const char *value;
};

static const struct starfive_vf2_pro starfive_vera[] = {
	{"/soc/ethernet@16030000/mdio/ethernet-phy@0", "rx-internal-delay-ps",
		"1900"},
	{"/soc/ethernet@16030000/mdio/ethernet-phy@0", "tx-internal-delay-ps",
		"1350"}
};

static const struct starfive_vf2_pro starfive_verb[] = {
	{"/soc/ethernet@16030000", "starfive,tx-use-rgmii-clk", NULL},
	{"/soc/ethernet@16040000", "starfive,tx-use-rgmii-clk", NULL},

	{"/soc/ethernet@16030000/mdio/ethernet-phy@0",
		"motorcomm,tx-clk-adj-enabled", NULL},
	{"/soc/ethernet@16030000/mdio/ethernet-phy@0",
		"motorcomm,tx-clk-100-inverted", NULL},
	{"/soc/ethernet@16030000/mdio/ethernet-phy@0",
		"motorcomm,tx-clk-1000-inverted", NULL},
	{"/soc/ethernet@16030000/mdio/ethernet-phy@0",
		"rx-internal-delay-ps", "1900"},
	{"/soc/ethernet@16030000/mdio/ethernet-phy@0",
		"tx-internal-delay-ps", "1500"},

	{"/soc/ethernet@16040000/mdio/ethernet-phy@1",
		"motorcomm,tx-clk-adj-enabled", NULL},
	{ "/soc/ethernet@16040000/mdio/ethernet-phy@1",
		"motorcomm,tx-clk-100-inverted", NULL},
	{"/soc/ethernet@16040000/mdio/ethernet-phy@1",
		"rx-internal-delay-ps", "0"},
	{"/soc/ethernet@16040000/mdio/ethernet-phy@1",
		"tx-internal-delay-ps", "0"},
};

void spl_fdt_fixup_version_a(void *fdt)
{
	u32 phandle;
	u8 i;
	int offset;
	int ret;

	fdt_setprop_string(fdt, fdt_path_offset(fdt, "/"), "model",
			   "StarFive VisionFive 2 v1.2A");

	offset = fdt_path_offset(fdt, "/soc/clock-controller@13020000");
	phandle = fdt_get_phandle(fdt, offset);
	offset = fdt_path_offset(fdt, "/soc/ethernet@16040000");

	fdt_setprop_u32(fdt, offset, "assigned-clocks", phandle);
	fdt_appendprop_u32(fdt, offset, "assigned-clocks", JH7110_SYSCLK_GMAC1_TX);
	fdt_appendprop_u32(fdt, offset, "assigned-clocks", phandle);
	fdt_appendprop_u32(fdt, offset, "assigned-clocks", JH7110_SYSCLK_GMAC1_RX);

	fdt_setprop_u32(fdt, offset,  "assigned-clock-parents", phandle);
	fdt_appendprop_u32(fdt, offset,  "assigned-clock-parents",
			   JH7110_SYSCLK_GMAC1_RMII_RTX);
	fdt_appendprop_u32(fdt, offset,  "assigned-clock-parents", phandle);
	fdt_appendprop_u32(fdt, offset,  "assigned-clock-parents",
			   JH7110_SYSCLK_GMAC1_RMII_RTX);

	fdt_setprop_string(fdt, fdt_path_offset(fdt, "/soc/ethernet@16040000"),
			   "phy-mode", "rmii");

	for (i = 0; i < ARRAY_SIZE(starfive_vera); i++) {
		offset = fdt_path_offset(fdt, starfive_vera[i].path);

		if (starfive_vera[i].value)
			ret = fdt_setprop_u32(fdt, offset,  starfive_vera[i].name,
					      dectoul(starfive_vera[i].value, NULL));
		else
			ret = fdt_setprop_empty(fdt, offset, starfive_vera[i].name);

		if (ret) {
			pr_err("%s set prop %s fail.\n", __func__, starfive_vera[i].name);
				break;
		}
	}
}

void spl_fdt_fixup_version_b(void *fdt)
{
	u32 phandle;
	u8 i;
	int offset;
	int ret;

	fdt_setprop_string(fdt, fdt_path_offset(fdt, "/"), "model",
			   "StarFive VisionFive 2 v1.3B");

	/* gmac0 */
	offset = fdt_path_offset(fdt, "/soc/clock-controller@17000000");
	phandle = fdt_get_phandle(fdt, offset);
	offset = fdt_path_offset(fdt, "/soc/ethernet@16030000");

	fdt_setprop_u32(fdt, offset, "assigned-clocks", phandle);
	fdt_appendprop_u32(fdt, offset, "assigned-clocks", JH7110_AONCLK_GMAC0_TX);
	fdt_setprop_u32(fdt, offset,  "assigned-clock-parents", phandle);
	fdt_appendprop_u32(fdt, offset,  "assigned-clock-parents",
			   JH7110_AONCLK_GMAC0_RMII_RTX);

	/* gmac1 */
	offset = fdt_path_offset(fdt, "/soc/clock-controller@13020000");
	phandle = fdt_get_phandle(fdt, offset);
	offset = fdt_path_offset(fdt, "/soc/ethernet@16040000");

	fdt_setprop_u32(fdt, offset, "assigned-clocks", phandle);
	fdt_appendprop_u32(fdt, offset, "assigned-clocks", JH7110_SYSCLK_GMAC1_TX);
	fdt_setprop_u32(fdt, offset,  "assigned-clock-parents", phandle);
	fdt_appendprop_u32(fdt, offset,  "assigned-clock-parents",
			   JH7110_SYSCLK_GMAC1_RMII_RTX);

	for (i = 0; i < ARRAY_SIZE(starfive_verb); i++) {
		offset = fdt_path_offset(fdt, starfive_verb[i].path);

		if (starfive_verb[i].value)
			ret = fdt_setprop_u32(fdt, offset,  starfive_verb[i].name,
					      dectoul(starfive_verb[i].value, NULL));
		else
			ret = fdt_setprop_empty(fdt, offset, starfive_verb[i].name);

		if (ret) {
			pr_err("%s set prop %s fail.\n", __func__, starfive_verb[i].name);
				break;
		}
	}
}

void spl_perform_fixups(struct spl_image_info *spl_image)
{
	u8 version;

	version = get_pcb_revision_from_eeprom();
	switch (version) {
	case 'a':
	case 'A':
		spl_fdt_fixup_version_a(spl_image->fdt_addr);
		break;

	case 'b':
	case 'B':
	default:
		spl_fdt_fixup_version_b(spl_image->fdt_addr);
		break;
	};

	/* Update the memory size which read form eeprom or DT */
	fdt_fixup_memory(spl_image->fdt_addr, 0x40000000, gd->ram_size);
}
int spl_board_init_f(void)
{
	int ret;

	ret = spl_soc_init();
	if (ret) {
		debug("JH7110 SPL init failed: %d\n", ret);
		return ret;
	}

	return 0;
}

u32 spl_boot_device(void)
{
	u32 mode;

	mode = in_le32(JH7110_BOOT_MODE_SELECT_REG)
				& JH7110_BOOT_MODE_SELECT_MASK;
	switch (mode) {
	case 0:
		return BOOT_DEVICE_SPI;

	case 1:
		return BOOT_DEVICE_MMC2;

	case 2:
		return BOOT_DEVICE_MMC1;

	case 3:
		return BOOT_DEVICE_UART;

	default:
		debug("Unsupported boot device 0x%x.\n", mode);
		return BOOT_DEVICE_NONE;
	}
}

void board_init_f(ulong dummy)
{
	int ret;

	ret = spl_early_init();
	if (ret)
		panic("spl_early_init() failed: %d\n", ret);

	riscv_cpu_setup(NULL, NULL);
	preloader_console_init();

	/* Set the parent clock of cpu_root clock to pll0,
	 * it must be initialized here
	 */
	clrsetbits_le32(JH7110_SYS_CRG + JH7110_CLK_CPU_ROOT_OFFSET,
			JH7110_CLK_CPU_ROOT_MASK,
			BIT(JH7110_CLK_CPU_ROOT_SHIFT));

	ret = spl_board_init_f();
	if (ret) {
		debug("spl_board_init_f init failed: %d\n", ret);
		return;
	}
}

#if CONFIG_IS_ENABLED(SPL_LOAD_FIT)
int board_fit_config_name_match(const char *name)
{
	/* boot using first FIT config */
	return 0;
}
#endif
