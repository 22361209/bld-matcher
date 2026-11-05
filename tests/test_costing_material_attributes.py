import unittest

from app.modules.costing.material_attributes import suggested_material_attribute


class CostingMaterialAttributeTests(unittest.TestCase):
    def test_purchased_materials_recognize_names_aliases_and_section_specific_codes(self):
        for category, name in [
            ("衬套", "B8012"), ("衬套", "C8299 锌镍彩"), ("配件", "大胶套"), ("球头", "球肖 磷化"),
            ("球销", "25球 磷化"), ("球头1", "22球 磷化"), ("球头1", "双层球座\nB-081\nC-039.2"),
            ("球座", "明威35C-4"), ("球头", "28C-3"), ("球头1", "16#"), ("护套", "HT16"),
            ("球头", "防尘罩"), ("吊杆衬套", "白色塑料护套*2"), ("卡环", "下卡环"),
            ("球头", "球壳C卡 M30*40*1.5"), ("标准件", "平垫圈"),
            ("安装", "铆钉圆头10*30*16"), ("支架", "滚花螺栓M12*1.25*47 磷化"),
            ("球头", "螺帽M10*1.25法兰自锁"), ("球头", "防尘罩下卡簧32*2 镀白"),
        ]:
            with self.subTest(category=category, name=name):
                self.assertEqual(suggested_material_attribute({"category": category, "name": name}), "外购")

    def test_packaging_before_or_after_carton_identity_normalization(self):
        for category, name in [("外箱", "54*41*15.5"), ("外包装", "纸箱"), ("外箱", "内外箱"),
                               ("包装", "56×43×14.5"), ("包装", "内盒"), ("内包装", ""), ("球头1", "内袋")]:
            with self.subTest(category=category, name=name):
                self.assertEqual(suggested_material_attribute({"category": category, "name": name}), "外购")

    def test_surface_processing_in_part_or_process_sections(self):
        for category, name in [("表面处理", "B8328"), ("焊接组件", "电泳"), ("焊接组件", "喷砂"),
                               ("衬套", "底座电泳"), ("衬套", "衬套做锌镍蓝 2支"), ("球头", "镀白"),
                               ("球壳", "表面处理（镀蓝白）"), ("球销", "球销磷化加工费")]:
            with self.subTest(category=category, name=name):
                self.assertEqual(suggested_material_attribute({"category": category, "name": name}), "委外加工")

    def test_only_confirmed_outsourced_fee_names_override_general_labor(self):
        for name in ("委外拉伸工费", "焊接工费 委外"):
            with self.subTest(name=name):
                self.assertEqual(suggested_material_attribute({"name": name}), "委外加工")
        for name in ("拉伸工费", "焊接工费", "非委外拉伸工费", "焊接工费未委外",
                     "焊接工费委外待确认", "不含委外拉伸工费", "委外运费"):
            with self.subTest(name=name):
                self.assertEqual(suggested_material_attribute({"name": name}), "自制")
        self.assertEqual(suggested_material_attribute({"name": "委外采购螺母"}), "外购")

    def test_work_and_unlisted_components_stay_self_made(self):
        for category, name in [
            ("安装", "装左右底座衬套"), ("球头", "装护套"), ("球头1", "上防尘罩"),
            ("球头", "打紧螺栓螺母"), ("安装", "压装滚花螺栓"), ("球头", "塑料挡圈压入球销"),
            ("安装工费", "球销-球座-球壳"), ("焊接组件", "铆接工费"), ("包装", "包装工费"),
            ("衬套", "底座焊接管"), ("衬套", "大衬套U型支架"), ("焊接组件", "螺母支架"),
            ("球头", "Q8298"), ("球头", "球壳毛坯"), ("球头1", "封板"), ("球销", "油脂"),
            ("焊接组件", "56*43*14.5"), ("", ""), ("", "未识别项目"),
        ]:
            with self.subTest(category=category, name=name):
                self.assertEqual(suggested_material_attribute({"category": category, "name": name}), "自制")
