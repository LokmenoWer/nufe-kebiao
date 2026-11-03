package wiki.nufe.kebiao.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ProfileTest {
    @Test
    fun onlyKeepsIdentityFieldsFromProfile() {
        val profile = Profile.parse(
            """
            {
              "student": {
                "name": "张三",
                "code": "2023001",
                "department": "计算机与人工智能学院",
                "major": "计算机科学与技术",
                "adminclass": "计算机2301",
                "grade": "2023"
              },
              "mail": {"bound": true, "email": "z***n@example.com"}
            }
            """.trimIndent(),
        )

        assertEquals("张三", profile.student?.name)
        assertEquals("2023001", profile.student?.code)
        assertEquals("z***n@example.com", profile.mail?.email)
        assertTrue(profile.mail?.bound == true)
    }
}
