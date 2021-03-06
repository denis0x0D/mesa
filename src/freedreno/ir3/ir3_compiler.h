/*
 * Copyright (C) 2013 Rob Clark <robclark@freedesktop.org>
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice (including the next
 * paragraph) shall be included in all copies or substantial portions of the
 * Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 *
 * Authors:
 *    Rob Clark <robclark@freedesktop.org>
 */

#ifndef IR3_COMPILER_H_
#define IR3_COMPILER_H_

#include "util/disk_cache.h"

#include "ir3.h"

struct ir3_ra_reg_set;
struct ir3_shader;

struct ir3_compiler {
	struct fd_device *dev;
	uint32_t gpu_id;
	struct ir3_ra_reg_set *set;
	struct ir3_ra_reg_set *mergedregs_set;
	uint32_t shader_count;

	struct disk_cache *disk_cache;

	/*
	 * Configuration options for things that are handled differently on
	 * different generations:
	 */

	/* a4xx (and later) drops SP_FS_FLAT_SHAD_MODE_REG_* for flat-interpolate
	 * so we need to use ldlv.u32 to load the varying directly:
	 */
	bool flat_bypass;

	/* on a3xx, we need to add one to # of array levels:
	 */
	bool levels_add_one;

	/* on a3xx, we need to scale up integer coords for isaml based
	 * on LoD:
	 */
	bool unminify_coords;

	/* on a3xx do txf_ms w/ isaml and scaled coords: */
	bool txf_ms_with_isaml;

	/* on a4xx, for array textures we need to add 0.5 to the array
	 * index coordinate:
	 */
	bool array_index_add_half;

	/* on a6xx, rewrite samgp to sequence of samgq0-3 in vertex shaders:
	 */
	bool samgq_workaround;

	/* The maximum number of constants, in vec4's, across the entire graphics
	 * pipeline.
	 */
	uint16_t max_const_pipeline;

	/* The maximum number of constants, in vec4's, for VS+HS+DS+GS. */
	uint16_t max_const_geom;

	/* The maximum number of constants, in vec4's, for FS. */
	uint16_t max_const_frag;

	/* A "safe" max constlen that can be applied to each shader in the
	 * pipeline which we guarantee will never exceed any combined limits.
	 */
	uint16_t max_const_safe;

	/* The maximum number of constants, in vec4's, for compute shaders. */
	uint16_t max_const_compute;

	/* on a3xx, the unit of indirect const load is higher than later gens (in
	 * vec4 units):
	 */
	uint32_t const_upload_unit;
};

void ir3_compiler_destroy(struct ir3_compiler *compiler);
struct ir3_compiler * ir3_compiler_create(struct fd_device *dev, uint32_t gpu_id);

void ir3_disk_cache_init(struct ir3_compiler *compiler);
void ir3_disk_cache_init_shader_key(struct ir3_compiler *compiler,
		struct ir3_shader *shader);
bool ir3_disk_cache_retrieve(struct ir3_compiler *compiler,
		struct ir3_shader_variant *v);
void ir3_disk_cache_store(struct ir3_compiler *compiler,
		struct ir3_shader_variant *v);

int ir3_compile_shader_nir(struct ir3_compiler *compiler,
		struct ir3_shader_variant *so);

/* gpu pointer size in units of 32bit registers/slots */
static inline
unsigned ir3_pointer_size(struct ir3_compiler *compiler)
{
	return (compiler->gpu_id >= 500) ? 2 : 1;
}

enum ir3_shader_debug {
	IR3_DBG_SHADER_VS  = BITFIELD_BIT(0),
	IR3_DBG_SHADER_TCS = BITFIELD_BIT(1),
	IR3_DBG_SHADER_TES = BITFIELD_BIT(2),
	IR3_DBG_SHADER_GS  = BITFIELD_BIT(3),
	IR3_DBG_SHADER_FS  = BITFIELD_BIT(4),
	IR3_DBG_SHADER_CS  = BITFIELD_BIT(5),
	IR3_DBG_DISASM     = BITFIELD_BIT(6),
	IR3_DBG_OPTMSGS    = BITFIELD_BIT(7),
	IR3_DBG_FORCES2EN  = BITFIELD_BIT(8),
	IR3_DBG_NOUBOOPT   = BITFIELD_BIT(9),
	IR3_DBG_NOFP16     = BITFIELD_BIT(10),
	IR3_DBG_NOCACHE    = BITFIELD_BIT(11),

	/* DEBUG-only options: */
	IR3_DBG_SCHEDMSGS  = BITFIELD_BIT(20),
	IR3_DBG_RAMSGS     = BITFIELD_BIT(21),
};

extern enum ir3_shader_debug ir3_shader_debug;

static inline bool
shader_debug_enabled(gl_shader_stage type)
{
	if (ir3_shader_debug & IR3_DBG_DISASM)
		return true;

	switch (type) {
	case MESA_SHADER_VERTEX:      return !!(ir3_shader_debug & IR3_DBG_SHADER_VS);
	case MESA_SHADER_TESS_CTRL:   return !!(ir3_shader_debug & IR3_DBG_SHADER_TCS);
	case MESA_SHADER_TESS_EVAL:   return !!(ir3_shader_debug & IR3_DBG_SHADER_TES);
	case MESA_SHADER_GEOMETRY:    return !!(ir3_shader_debug & IR3_DBG_SHADER_GS);
	case MESA_SHADER_FRAGMENT:    return !!(ir3_shader_debug & IR3_DBG_SHADER_FS);
	case MESA_SHADER_COMPUTE:     return !!(ir3_shader_debug & IR3_DBG_SHADER_CS);
	default:
		debug_assert(0);
		return false;
	}
}

static inline void
ir3_debug_print(struct ir3 *ir, const char *when)
{
	if (ir3_shader_debug & IR3_DBG_OPTMSGS) {
		printf("%s:\n", when);
		ir3_print(ir);
	}
}

#endif /* IR3_COMPILER_H_ */
